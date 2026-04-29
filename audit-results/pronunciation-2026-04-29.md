# Pronunciation Audit — 2026-04-29

**Voice:** Johanna (`HHKcxM1mAt4nEB2ZjrRw`)  
**Model:** `eleven_multilingual_v2`  
**Settings:** stability=0.45, similarity=0.8, style=0.15, speed=1.0  
**Pronunciation Dict:** IW2jbKiFNq8Nz0X4kscp (v 4DMpzFxwGeOmuu6q7Knl)  
**MP3 directory:** `audit-results/audio-2026-04-29/`

**Result:** 14/17 PASS

| # | Phrase | Erwartet (mind. eines) | Gehört (STT) | Status | MP3 |
|---|---|---|---|---|---|
| 1 | Guten Tag, hier ist Lisa vom Fachwelt Verlag. | `fachwelt` / `verlag` | Guten Tag, hier ist Lisa vom Fachweltverlag. | **PASS** | `01_guten_tag_hier_ist_lisa_vom_fachwelt_verlag.mp3` |
| 2 | zweitausendsechsundzwanzig | `zweitausendsechsundzwanzig` / `zwei tausend sechs und zwanzig` | 2026. | **FAIL** | `02_zweitausendsechsundzwanzig.mp3` |
| 3 | fachwelt punkt de | `fachwelt punkt de` / `fachwelt.de` | Fachweltpunkte. | **FAIL** | `03_fachwelt_punkt_de.mp3` |
| 4 | fachwelt-marketpleis punkt de | `marketpleis` / `marketplace` | Fachweltmarketplace-Punkte? | **PASS** | `04_fachwelt_marketpleis_punkt_de.mp3` |
| 5 | Marketpleis | `marketpleis` / `marketplace` | Marketplace. | **PASS** | `05_marketpleis.mp3` |
| 6 | Wir bauen einen Marketpleis für B2B-Hersteller. | `marketpleis` / `marketplace` | Wir bauen einen Marketplace für BE zu Behersteller. | **PASS** | `06_wir_bauen_einen_marketpleis_für_b2b_hersteller.mp3` |
| 7 | Es geht um B2B. | `be zu be` / `be-zu-be` / `b2b` | Es geht um b' Sube. | **FAIL** | `07_es_geht_um_b2b.mp3` |
| 8 | Ich bin ein KI-Assistent vom Fachwelt Verlag. | `ki` / `ka i` / `ka-i` | Ich bin ein KI Assistent vom Fachweltverlag. | **PASS** | `08_ich_bin_ein_ki_assistent_vom_fachwelt_verlag.mp3` |
| 9 | Vorab-Registrierung. | `vorab` / `registrierung` | Vorabregistrierung? | **PASS** | `09_vorab_registrierung.mp3` |
| 10 | Außendienst-Mitarbeiterin. | `außendienst` / `mitarbeiterin` | Außendienstmitarbeiterin. | **PASS** | `10_außendienst_mitarbeiterin.mp3` |
| 11 | Verlagsverzeichnis. | `verlagsverzeichnis` / `verzeichnis` | Verlagsverzeichnis. | **PASS** | `11_verlagsverzeichnis.mp3` |
| 12 | Sales-Pipeline. | `seels` / `sales` / `peipläjn` / `pipeline` | Sales Pipeline. | **PASS** | `12_sales_pipeline.mp3` |
| 13 | CEO und CRM. | `ze e o` / `ceo` / `ze er em` / `crm` | CEO und CRM. | **PASS** | `13_ceo_und_crm.mp3` |
| 14 | Lead generieren. | `lied` / `lead` | Lead generieren. | **PASS** | `14_lead_generieren.mp3` |
| 15 | Hätten Sie kurz zwei Minuten? | `zwei minuten` | "Hätten Sie kurz zwei Minuten?" | **PASS** | `15_hätten_sie_kurz_zwei_minuten.mp3` |
| 16 | max punkt mustermann at firma minus beispiel punkt de | `mustermann` / `firma` | Max Punkt Mustermann at Firma minus Beispielpunkte. | **PASS** | `16_max_punkt_mustermann_at_firma_minus_beispiel_punkt_de.mp3` |
| 17 | Im September zweitausendsechsundzwanzig startet der Marketpleis. | `september` / `marketpleis` / `marketplace` | Im September zweitausendsechsundzwanzig startet der Marketplace. | **PASS** | `17_im_september_zweitausendsechsundzwanzig_startet_der_marketpl.mp3` |

## Fail-Analyse — Alias-Vorschläge

- `zweitausendsechsundzwanzig`
  - Erwartet: ['zweitausendsechsundzwanzig', 'zwei tausend sechs und zwanzig']
  - Gehört: `2026.`
  - **Vorschlag:** PLS-Alias in Dict `IW2jbKiFNq8Nz0X4kscp` ergänzen, neue Version-ID in `src/agent.py` einsetzen. Edon's Decision — nicht automatisch.
- `fachwelt punkt de`
  - Erwartet: ['fachwelt punkt de', 'fachwelt.de']
  - Gehört: `Fachweltpunkte.`
  - **Vorschlag:** PLS-Alias in Dict `IW2jbKiFNq8Nz0X4kscp` ergänzen, neue Version-ID in `src/agent.py` einsetzen. Edon's Decision — nicht automatisch.
- `Es geht um B2B.`
  - Erwartet: ['be zu be', 'be-zu-be', 'b2b']
  - Gehört: `Es geht um b' Sube.`
  - **Vorschlag:** PLS-Alias in Dict `IW2jbKiFNq8Nz0X4kscp` ergänzen, neue Version-ID in `src/agent.py` einsetzen. Edon's Decision — nicht automatisch.