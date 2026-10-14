# 7b — bounded Actions artifact/proof transport

- Reusable read-only adapter for Actions object collections, run attempts, successful
  exact job proof and artifact JSON. This does not authorize owner decisions yet.
- Verify complete pagination/total_count; cap below1000 API filtered results and
  reject changing/incomplete collections or ambiguous artifacts/jobs.
- Artifact metadata binds run/repository/head, nonexpired object, digest and size.
  Download only fixed GitHub artifact API → signed HTTPS URL, no credentials/cookies
  on blob client, no further redirects. Sanitize all transport/archive errors.
- Stream archive<=2MB, verify SHA256, exact single expected filename, no filesystem
  extraction/execution; JSON object. Decision member<=512KB; original analysis may
  explicitly use the existing<=2MB artifact contract. No key/model calls.
- Tests cover accepted archive, malformed/oversize/bomb-like data, path/extra member,
  digest/origin mismatch, unsafe redirects, auth separation and complete attempt jobs.
