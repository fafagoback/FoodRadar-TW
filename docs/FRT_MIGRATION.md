# FRT / FoodRadar-TW
FR is the source of the Raw schema, dynamic menu workers, compact SQLite, price rules and static data contract.
FRT retains the original grid points; the pinned FR township geometry fills uncovered districts, including Kinmen and Lienchiang.
The 15 discovery workers produce the same FR Feed HTTP bodies and store entries. The reducer verifies every planned point, globally deduplicates canonical UUIDs and initializes the separate FRT Supabase queue.
HF target: hub-google/Foodradar-TW/Raw. The old hub-google/UberEat dataset is untouched; this version starts a clean FR-format history.
Schedule: 06:30 and 18:30 Taiwan time, then pages only after successful data/quality gate; cleanup at 03:00.
The first national run must validate capacity and speed; the FR static size safety limit is intentionally unchanged.

FRT has an independent checkpoint provenance: FR historical checkpoints are not imported. Unknown contract revisions fail closed and rebuild from the new FRT Raw archive.
