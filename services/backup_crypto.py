"""Encrypted copy of a database backup: AES-256-GCM in chunks, the key derived from the owner's passphrase with Argon2id.

File format (every number big-endian):
    MAGIC (6) | salt (16) | nonce prefix (4) | chunk size (4) | then records: length (4) | AES-GCM ciphertext + tag
Each record is one chunk. Its nonce is the prefix + the chunk number; its associated data is the header + the chunk number + a "last chunk" flag, so a changed, reordered, cut-off or
extended file is refused (a file that was truncated loses the chunk that carries the last flag). Nothing here reads the database, the keyring or the network, and the passphrase and the
key are never logged or put in an error message."""
import os
import struct

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"MGBK1\n"
SALT_BYTES, PREFIX_BYTES, KEY_BYTES = 16, 4, 32
CHUNK_BYTES = 1024 * 1024
MIN_PASSPHRASE = 12
HEADER = struct.Struct(">6s16s4sI")
LENGTH = struct.Struct(">I")


class BackupCryptoError(RuntimeError):
    pass


def _key(passphrase, salt):
    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE:
        raise BackupCryptoError(f"the passphrase must have at least {MIN_PASSPHRASE} characters")
    return hash_secret_raw(passphrase.encode("utf-8"), salt, time_cost=3, memory_cost=64 * 1024, parallelism=4, hash_len=KEY_BYTES, type=Type.ID)


def _nonce(prefix, number):
    return prefix + struct.pack(">Q", number)


def _aad(header, number, last):
    return header + struct.pack(">Q?", number, last)


def encrypt_file(source, target, passphrase, *, chunk_bytes=CHUNK_BYTES):
    """Encrypt `source` into `target` (written in place; the caller uses a temporary name and renames after checking). Returns the number of bytes written."""
    salt, prefix = os.urandom(SALT_BYTES), os.urandom(PREFIX_BYTES)
    header = HEADER.pack(MAGIC, salt, prefix, chunk_bytes)
    cipher = AESGCM(_key(passphrase, salt))
    written = len(header)
    with open(source, "rb") as reader, open(target, "wb") as writer:
        writer.write(header)
        number, pending = 0, reader.read(chunk_bytes)
        while True:
            following = reader.read(chunk_bytes) if len(pending) == chunk_bytes else b""
            last = not following
            record = cipher.encrypt(_nonce(prefix, number), pending, _aad(header, number, last))
            writer.write(LENGTH.pack(len(record)) + record)
            written += LENGTH.size + len(record)
            if last:
                break
            number, pending = number + 1, following
        writer.flush()
        os.fsync(writer.fileno())
    return written


def decrypt_chunks(source, passphrase):
    """Yield the plaintext chunks of an encrypted backup; raises BackupCryptoError for a wrong passphrase, a damaged, cut-off or foreign file."""
    with open(source, "rb") as reader:
        header = reader.read(HEADER.size)
        if len(header) != HEADER.size:
            raise BackupCryptoError("the file is not an encrypted backup")
        magic, salt, prefix, chunk_bytes = HEADER.unpack(header)
        if magic != MAGIC or not 1024 <= chunk_bytes <= 64 * 1024 * 1024:
            raise BackupCryptoError("the file is not an encrypted backup")
        cipher = AESGCM(_key(passphrase, salt))
        number, raw = 0, reader.read(LENGTH.size)
        if not raw:
            raise BackupCryptoError("the encrypted backup is cut off")
        while raw:
            if len(raw) != LENGTH.size:
                raise BackupCryptoError("the encrypted backup is cut off")
            (size,) = LENGTH.unpack(raw)
            record = reader.read(size)
            if size > chunk_bytes + 16 or len(record) != size:
                raise BackupCryptoError("the encrypted backup is damaged or cut off")
            raw = reader.read(LENGTH.size)
            try:
                yield cipher.decrypt(_nonce(prefix, number), record, _aad(header, number, not raw))
            except InvalidTag:
                raise BackupCryptoError("wrong passphrase, or the encrypted backup is damaged or cut off") from None
            number += 1


def decrypt_file(source, target, passphrase):
    """Decrypt into `target`; returns the plaintext size. A failure leaves a partial target that the caller must remove."""
    total = 0
    with open(target, "wb") as writer:
        for chunk in decrypt_chunks(source, passphrase):
            writer.write(chunk)
            total += len(chunk)
    return total


def verify_file(source, passphrase):
    """Read the whole encrypted file, checking every chunk's authentication; returns the plaintext size without writing it anywhere."""
    return sum(len(chunk) for chunk in decrypt_chunks(source, passphrase))
