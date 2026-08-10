# Amex amount-sign — thorough sample matrix + UI playthrough

Matrix: **PASS** (8/8 samples)

| Sample | amount_sign | Rows | Result |
| --- | --- | --- | --- |
| `user-reported-amex` User-reported Amex 2-row sample | `positive_is_outgoing` (expected `positive_is_outgoing`) | 2 | **PASS** |
| `amex-with-payment` Amex charges + payment credit | `positive_is_outgoing` (expected `positive_is_outgoing`) | 3 | **PASS** |
| `amex-currency-parens` Amex with £ symbols, commas, paren credit | `positive_is_outgoing` (expected `positive_is_outgoing`) | 3 | **PASS** |
| `amex-extra-columns` Amex-style Card Member / Account # columns | `positive_is_outgoing` (expected `positive_is_outgoing`) | 3 | **PASS** |
| `revolut-signed` Revolut-like negative spend | `negative_is_outgoing` (expected `negative_is_outgoing`) | 3 | **PASS** |
| `accounting-generic` Generic Date/Description/Amount accounting (neg=out) | `negative_is_outgoing` (expected `negative_is_outgoing`) | 3 | **PASS** |
| `money-columns` Paid in / Paid out columns | `absolute` (expected `absolute`) | 2 | **PASS** |
| `direction-column` Explicit Direction column | `negative_is_outgoing` (expected `None`) | 2 | **PASS** |

## Row-level checks

### User-reported Amex 2-row sample (`user-reported-amex`)

- [PASS] `GO AHEAD GROUP          LONDON` → expected outgoing £36.6, got outgoing £36.6
- [PASS] `SAINSBURY'S SUPERMARKET CAMBRIDGE` → expected outgoing £19.55, got outgoing £19.55

### Amex charges + payment credit (`amex-with-payment`)

- [PASS] `TESCO STORES` → expected outgoing £24.99, got outgoing £24.99
- [PASS] `PAYMENT RECEIVED - THANK YOU` → expected incoming £150.0, got incoming £150.0
- [PASS] `COFFEE SHOP` → expected outgoing £4.5, got outgoing £4.5

### Amex with £ symbols, commas, paren credit (`amex-currency-parens`)

- [PASS] `WAITROSE` → expected outgoing £12.4, got outgoing £12.4
- [PASS] `AMAZON.CO.UK` → expected outgoing £1234.56, got outgoing £1234.56
- [PASS] `MERCHANT REFUND` → expected incoming £12.0, got incoming £12.0

### Amex-style Card Member / Account # columns (`amex-extra-columns`)

- [PASS] `TESCO STORES 2897` → expected outgoing £28.91, got outgoing £28.91
- [PASS] `TFL TRAVEL CHARGE` → expected outgoing £3.5, got outgoing £3.5
- [PASS] `PAYMENT RECEIVED - THANK YOU` → expected incoming £200.0, got incoming £200.0

### Revolut-like negative spend (`revolut-signed`)

- [PASS] `REVOLUT*COFFEE` → expected outgoing £4.8, got outgoing £4.8
- [PASS] `SUPERMARKET` → expected outgoing £32.15, got outgoing £32.15
- [PASS] `TOPUP JOHN` → expected incoming £50.0, got incoming £50.0

### Generic Date/Description/Amount accounting (neg=out) (`accounting-generic`)

- [PASS] `Coffee` → expected outgoing £4.8, got outgoing £4.8
- [PASS] `Rent` → expected outgoing £850.0, got outgoing £850.0
- [PASS] `Salary` → expected incoming £2800.0, got incoming £2800.0

### Paid in / Paid out columns (`money-columns`)

- [PASS] `Salary` → expected incoming £2000.0, got incoming £2000.0
- [PASS] `Groceries` → expected outgoing £45.2, got outgoing £45.2

### Explicit Direction column (`direction-column`)

- [PASS] `Shop` → expected outgoing £12.5, got outgoing £12.5
- [PASS] `Refund` → expected incoming £3.0, got incoming £3.0

## UI playthrough

- Home import ready with July 2026 period
- Selected user-reported Amex CSV + Source=Amex
- Amex user sample preview: both charges classified Outgoing (summary snippet: 'ROWS\n2\nIN\n£0.00\nOUT\n£56.15\nNET\n£-56.15\n\nStats month: 2026-07 · Range: 2026-07-01 → 2026-07-31 · Kept 2 / 2 extracted · Transfer match preview: 0 pair(s) in full')
- Amex charges+payment: TESCO/COFFEE outgoing, PAYMENT RECEIVED incoming
- Revolut control: negative spend still Outgoing; TOPUP Incoming
- Imported Amex user sample successfully

