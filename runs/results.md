| model | easy reward | medium reward | hard reward | expert reward | solved | bugs fixed | hard-coded cells / ep | collateral cells / ep | tool calls / ep |
|---|---|---|---|---|---|---|---|---|---|
| `claude-code/sonnet` | - | 1.00 (n=4) | 1.00 (n=4) | 1.00 (n=3) | 100% | 100% | 0.00 | 0.00 | 16.7 |
| `claude-code/opus` | - | 1.00 (n=4) | 1.00 (n=4) | - | 100% | 100% | 0.00 | 0.00 | 13.2 |
| `claude-code/haiku` | 1.00 (n=3) | 1.00 (n=4) | 1.00 (n=4) | 1.00 (n=3) | 100% | 100% | 0.00 | 0.00 | 25.9 |

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
| `anchor_slip` | 100% | 12 |
| `terminal_wrong_year` | 100% | 3 |
| `unit_mismatch` | 100% | 5 |
| `discount_off_by_one` | 100% | 3 |
| `terminal_not_discounted` | 100% | 5 |
| `sys_tax_on_ebitda` | 100% | 6 |
| `terminal_over_wacc` | 100% | 4 |
| `sys_capex_added` | 100% | 4 |

3 episode(s) excluded for provider/API errors.
