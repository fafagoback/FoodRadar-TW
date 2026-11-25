# FoodRadar-TW (FRT)

This is **FoodRadar with a different coordinate input and independent storage**.

- The **only application-core source** is [fafagoback/FoodRadar](https://github.com/fafagoback/FoodRadar). FRT does not check in copies of FR \`src/\`, \`scripts/\`, \`tests/\` or \`web/\`.
- \`frt/bootstrap.py\` fetches an **immutable tested FR commit** specified in \`config.json\` on every Actions runner. It copies FR source into the ephemeral runner, then applies only resource/coordinate configuration.
- \`data/seeds/taiwan_scan_points_3km_land_only.csv\` is exactly the original UE CSV (1,558 points), unchanged.
- \`frt/national_discovery.py\` is the **only FRT functional adapter**: scan original coordinates via FR's \`scan_single_point\`, deduplicate by canonical UUID, emit the native \`foodradar-shared-feed-v1\` manifest and immutable Raw bodies. From there onward the original FR dynamic task pool and release jobs run.
- FRT resources: Hugging Face \`hub-google/Foodradar-TW/Raw\`, separate Supabase \`frt-task-pool\`, separate \`crawler-state\` branch and GitHub Pages.
- Three workflows only: \`crawl.yml\` (01), \`pages.yml\` (02), \`cleanup.yml\` (03).
- The original \`hub-google/UberEat\` dataset and previous Git commit history are not deleted.

**Updating FR in FRT:** change only \`fr_core_sha\` in FRT's \`config.json\` to a new reviewed FR commit, run validation, then allow the normal schedule. Never copy application-core source into this repo.

A new FRT crawler must pass 1558-point preservation, 15-way merge/dedup, FR schema, HF target isolation, and the FR regression suite before deployment.
