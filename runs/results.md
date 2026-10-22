| model | easy reward | medium reward | hard reward | solved | bugs fixed | hard-coded cells / ep | collateral cells / ep | tool calls / ep |
|---|---|---|---|---|---|---|---|---|
| `claude-code/sonnet` | - | 1.00 (n=4) | 1.00 (n=4) | 100% | 100% | 0.00 | 0.00 | 14.1 |
| `claude-code/haiku` | 1.00 (n=3) | 1.00 (n=4) | 1.00 (n=4) | 100% | 100% | 0.00 | 0.00 | 22.5 |
| `claude-code/opus` | - | 1.00 (n=4) | 1.00 (n=4) | 100% | 100% | 0.00 | 0.00 | 13.2 |

| bug kind | fix rate (all models) | n |
|---|---|---|
| `tax_on_ebitda` | 100% | 15 |
| `range_omission` | 100% | 13 |
| `growth_chain_break` | 100% | 9 |
| `hardcoded_plug` | 100% | 8 |
| `crosslink_wrong` | 100% | 9 |
| `period_mislink` | 100% | 12 |
| `sign_flip` | 100% | 9 |
| `nwc_level_not_change` | 100% | 6 |
| `anchor_slip` | 100% | 6 |
| `terminal_wrong_year` | 100% | 3 |
| `unit_mismatch` | 100% | 3 |
| `discount_off_by_one` | 100% | 3 |
| `terminal_not_discounted` | 100% | 3 |

1 episode(s) excluded for provider/API errors.
