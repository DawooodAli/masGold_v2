"""Allowlisted diagnostic codes/messages: never derived from response bodies."""
MESSAGES = {
    "HTTP_AUTH": "رفض المصدر مفتاح API أو صلاحية الوصول (401/403). راجع المفتاح وصلاحيات الحساب.",
    "RATE_LIMIT": "تم تجاوز حد طلبات المصدر (429). انتظر قبل إعادة الاختبار.",
    "HTTP_ERROR": "أعاد المصدر خطأ في الاتصال. حاول لاحقاً.",
    "NETWORK": "تعذر الاتصال بالمصدر. راجع اتصال الإنترنت.",
    "TIMEOUT": "انتهت مهلة الاتصال بالمصدر. حاول مجدداً أو زد المهلة.",
    "KEY_MISSING": "مفتاح API غير مُعد. أدخل المفتاح أو احفظه أولاً.",
    "CREDENTIAL_STORE": "تعذر قراءة مفتاح API من مخزن بيانات الاعتماد الآمن.",
    "INVALID_JSON": "أعاد المصدر استجابة JSON غير صالحة.",
    "CURRENCY_MISMATCH": "عملة المصدر لا تطابق الجنيه المصري EGP.",
    "KARAT_MISMATCH": "عيار استجابة المصدر لا يطابق العيار المطلوب.",
    "SCHEMA_UNIT": "صيغة استجابة المصدر أو وحدتها لا تطابق عقد GoldTimo.",
    "QUOTE_VALIDATION": "أسعار المصدر أو توقيتها لا تجتاز التحقق.",
    "STALE": "تم الاتصال، لكن أسعار المصدر قديمة؛ لم تُعتمد أي أسعار.",
    "PERMISSION": "لا توجد صلاحية لإدارة الإعدادات أو انتهت جلسة الدخول.",
    "CONFIGURATION": "إعدادات رابط المصدر أو مهلة الاتصال غير صالحة.",
    "INTERNAL": "تعذر تنفيذ العملية؛ راجع الصلاحية والاتصال والإعدادات.",
}


class ConnectionTestError(ValueError):
    def __init__(self, code, http_status=None):
        self.code = code if code in MESSAGES else "INTERNAL"
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        super().__init__(MESSAGES[self.code])
