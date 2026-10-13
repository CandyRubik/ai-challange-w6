# 5a — восстановление summary

Base9fa12b6; PR13, head9f4f7d1, mergeb65b311.

- Standards0open, Spec0open. Обе оси нашли один и тот же P2: malformed freshness response после неизвестногоPOST терял uncertainty и разрешал финализатору создать дубль. Исправлен с сохранением UncertainSummaryPost для validation errors.
- 416 локальных тестов до последнего исправления, 7 focused после; GitHub CI420passed. AI Review run38463685151 success.
- 100 reviewable lines; optional-only trimming, actual provider messages95302bytes при96000, realAPI0 в offline measurement.
- Неизвестный принятый/непринятыйPOST отправляется только один раз; ошибка подтверждения ведёт к status-only finalization. Freshness guard сохраняется.
- Secret был настроен по явному разрешению владельца. Реальныйbad→clean smokePR12 записан в ci-smoke.json; smokeкод не слит.
