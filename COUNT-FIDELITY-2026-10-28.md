# Does a prompt's head count survive into the picture?

Grader: `deepseek-flash` via `https://api.deepseek.com/v1/chat/completions`. 14 pictures whose prompt states a count, one question each: "how many people are visible?".

`no-answer` rows are questions the model's own reasoning consumed the token budget on - a lost answer, not an abstention.

| picture | stated | counted | grader says | prompt |
|---|---|---|---|---|
| 20261024-120415_1024x1024_seed1234.png | 11 | 7 | 7 | The shot is wide, low, and held ju |
| 20261025-123301_1024x1024_seed702140921.png | 5 | 5 | 5 | five naked women floating in the a |
| 20261025-132012_1024x1024_seed168743412.png | 5 | 5 | 5 | five naked women floating in the a |
| 20261026-000549_1024x1024_seed188.png | 3 | 3 | 3 | three completely nude mature adult |
| 20261026-003040_1024x1024_seed100520156.png | 3 | 3 | 3 | three completely nude mature adult |
| 20261026-004806_256x256_seed100520169_07.png | 3 | 3 | 3 | zoom out wide from a distance cent |
| 20261026-011136_1024x1024_seed12.png | 3 | 3 | 3 | three very strong fit toned muscle |
| 20261026-014900_1024x1024_seed38.png | 3 | 3 | 3 | three mature older adult women clo |
| 20261026-033207_1024x1024_seed101.png | 3 | 6 | 6 | Pictured are women. Each clothed i |
| 20261026-040139_1024x1024_seed126.png | 3 | 12 | 12 | Pictured are women. Each clothed i |
| 20261026-113656_1024x1024_seed185.png | 3 | 3 | 3 | Character Dossiers (Before Scene E |
| 20261026-234323_1024x1024_seed1444.png | 2 | 2 | 2 | DESCRIPTION:
A photorealistic port |
| 20261027-175245_256x256_seed1244_11.png | 2 | 2 | 2 | DESCRIPTION:
A photorealistic port |
| 20261027-184051_256x256_seed1248_13.png | 2 | 2 | 2 | DESCRIPTION:
A photorealistic port |

**11 of 14 exact (79 %), 11 within one (79 %)); 0 unreadable (of which 0 lost to the budget); 0 errors.**

Median error: +0 figures; the grader under-counts 1, over-counts 2.
