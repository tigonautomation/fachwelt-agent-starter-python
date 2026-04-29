# Voice-Scout Phase 2 — Render + Filter

**Date:** 2026-04-29
**Test script:** "Guten Tag, hier ist Lisa vom Fachwelt Verlag. Kurzer Hinweis: ich bin eine KI, das Gespraech wird zur Qualitaetssicherung aufgezeichnet. Wir bauen einen Marktplatz fuer B2B-Hersteller — haetten Sie zwei Minuten?"
**Quality render model:** eleven_multilingual_v2
**Latency model:** eleven_flash_v2_5
**Settings:** stability=0.45, similarity_boost=0.8, style=0.15, speed=1.0
**Pronunciation pass:** Levenshtein ratio >= 0.92
**Latency pass:** median TTFB <= 500ms

## Results

| # | voice_id | name | levenshtein% | ttfb_ms (median) | pron_pass | latency_pass | overall |
|---|---|---|---|---|---|---|---|
| 1 | `rAmra0SCIYOxYmRNDSm3` | Lana Weiss - Soft and Sweet | 95.6% | 380 | ✓ | ✓ | PASS |
| 2 | `v3V1d2rk6528UrLKRuy8` | Susi - Effortless and Confident | 95.6% | 388 | ✓ | ✓ | PASS |
| 3 | `Qy4b2JlSGxY7I9M9Bqxb` | Laura  - Calm and Smooth | 95.6% | 392 | ✓ | ✓ | PASS |
| 4 | `WHaUUVTDq47Yqc9aDbkH` | Enniah - Friendly and Motivating | 96.1% | 440 | ✓ | ✓ | PASS |
| 5 | `HRIShmNY56JGHVU1vXIt` | Ela - Hopeful, Bright and Vibrant | 95.6% | 419 | ✓ | ✓ | PASS |
| 6 | `NE7AIW5DoJ7lUosXV2KR` | Ela - Cheerful and Happy | 96.1% | 511 | ✓ | ✗ | PRON-PASS |
| 7 | `pMrwpTuGOma7Nubxs5jo` | Lea - Warm and Supportive | 96.1% | 425 | ✓ | ✓ | PASS |
| 8 | `N8RXoLEWQWUCCrT8uDK7` | Emilia - Positive and Thoughtful | 95.6% | 408 | ✓ | ✓ | PASS |
| 9 | `M39iqBUcu1jyiwM5PfSy` | Lea - Genuine and Soothing | 95.1% | 447 | ✓ | ✓ | PASS |
| 10 | `YYDsZT3K2y6tv7X1aj6N` | Johanna - Professional and Strict | 93.1% | 405 | ✓ | ✓ | PASS |
| 11 | `Y5JXXvUD3rmjDInkLVA2` | Kerstin - Seducitve, Sensual and Silky | 95.6% | 500 | ✓ | ✓ | PASS |
| 12 | `yVKATr0ZJETwd3tQtpNG` | Julia - Confident and Friendly | 95.6% | 444 | ✓ | ✓ | PASS |
| 13 | `nGISSznGHAgSTKaMXEPO` | Irene -  Casual and Friendly | 95.6% | 451 | ✓ | ✓ | PASS |
| 14 | `a0CA83xXpwCwAaIpZXae` | Zen - Meditation german | 95.6% | 441 | ✓ | ✓ | PASS |
| 15 | `KFcKSkKkWqMVhCbLkuvh` | Kassandra  - Strange and Tricky | 95.6% | 404 | ✓ | ✓ | PASS |

## Top 5 (by levenshtein% among valid voices)

1. `WHaUUVTDq47Yqc9aDbkH` — Enniah - Friendly and Motivating — 96.1% — 440ms
2. `NE7AIW5DoJ7lUosXV2KR` — Ela - Cheerful and Happy — 96.1% — 511ms
3. `pMrwpTuGOma7Nubxs5jo` — Lea - Warm and Supportive — 96.1% — 425ms
4. `rAmra0SCIYOxYmRNDSm3` — Lana Weiss - Soft and Sweet — 95.6% — 380ms
5. `v3V1d2rk6528UrLKRuy8` — Susi - Effortless and Confident — 95.6% — 388ms