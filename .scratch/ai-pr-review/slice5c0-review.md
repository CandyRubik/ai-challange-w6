# 5c0 — общий formatter

Base6591d02; PR15, head9c47675, squash0110f36. Standards0open, Spec0open.

20 reviewable lines;58focused tests, GitHub CI и AI run38470124248 success. Default58KBcap сохраняется.

Первоначальный полный inline diff прошёл455localtests/self-review, но AI run38468485286 дал2InvalidModelResponse при92362/92698bytes и1881/1253completiontokens. Ответы не сохранялись; error не превратился в success. Общийnormalize_comment выделен сначала: formatter-context69401bytes и обе оси подтвердили самостоятельность.

Полный inline сохранён и восстановлен в следующем PR от новогоmain; строгаявалидация и CIлимиты не ослаблялись.
