# To do

## Frequency drift (software only, no hardware changes)

Needs boards on USB: wait until they are plugged in before starting.

- [ ] **Auto-net.** When the receiver's AFC locks on the other station, set the transmit offset so the reply goes out on the
  frequency it was heard on. The crystal offset between boards (up to ~13 kHz) then cancels, since one board's error applies
  equally to its receive and transmit. Lives in `trx_board.py`; the records' ±19 kHz offset range covers the worst case.
- [ ] **Per-board warm-up pre-compensation.** Transmit a long carrier from board A, log its frequency over time with board B,
  fit the key-on curve (roughly f(t) = A·(1 − e^(−t/τ)); cold boards drift 110–190 Hz/s at first), and apply the opposite
  curve to A's transmit offset from key-on. Store the curve per board, keyed by serial number or MAC address.
- [ ] **Die temperature compensation.** Have the firmware read the ESP32-S3's internal temperature sensor and report it in
  the transmit status, then correct the transmit frequency by temperature instead of time since key-on (handles boards that
  are already warm or have cooled between overs). Needs a firmware change and a frequency-vs-temperature calibration per board.
