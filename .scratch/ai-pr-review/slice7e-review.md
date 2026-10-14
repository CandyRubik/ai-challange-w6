# 7e — expiry sweep

- Finald17cbca796e89657d1696776d310b37d8bf3993a vs734ecb3f0ebd69e28f6df743ba3205104e681151;335reviewablelines.
-11expiry cases pass: normal/owner publisher, plain/valid/advisory-independent green, expired/lostauthority/malformedactor proof, queueoverflow and newer generation during read/write.
- Offline pre-final wire52064/96000,0realcalls. No model secret/import; schedule5min best effort, canonicalpublishproof and currentjournal guard.
- Fixed malformedmetadata leavinggreen and advisoryonlyacceptance creatingfalsegate. Bothaxesfinalfollow-up, livecombinedowner/replay/revoke/expiry smoke aftermerge.
