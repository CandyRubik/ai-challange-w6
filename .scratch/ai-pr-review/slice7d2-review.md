# 7d2 — authenticated application

- Finalruntime0d25e6c6ce2755148f496077b6edfe413eaf7a87 on1ceae44e7dd4f50b593373e3cf40a2008649b11f;264reviewable lines.
-14owner application tests pass,8presentation tests passed with prior runtime; prior combined37journal/application and66publishing/inline/debt pass.
- Fixed initial authority loss, empty journal changes during writes, stale empty display, debt lifecycle ValueError and partial original with empty journal. All invalidation/race repros are offline; original data unchanged.
- Standards0open on58a5aeb; final delta makes empty journal use common render/effects rather than shortcut. Spec final follow-up checks this delta. Workflow activation remainsnext.
