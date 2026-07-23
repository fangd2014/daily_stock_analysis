# PyTDX Data Foundation Sandbox

- Allowed implementation: `src/quant/`, `configs/quant/`, quant tests, README, changelog.
- Data providers: PyTDX minute bars with Tushare metadata and explicit Tushare/local fallback.
- Network-independent validator: `python -m pytest tests/test_quant_*.py -q`.
- No broker connection, order submission, return fabrication, or use of failed-quality data in optimization.
- Existing unrelated untracked files are outside this mission and must not be committed.
