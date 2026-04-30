from urllib.request import urlopen, Request
import json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

key = os.getenv('FMP_API_KEY', '')
print(f"Using key: {key[:6]}...{key[-4:] if len(key)>10 else key}\n")

tests = [
    ('v3/profile/AAPL',                    f'https://financialmodelingprep.com/api/v3/profile/AAPL?apikey={key}'),
    ('v3/earning_calendar (2026-05)',       f'https://financialmodelingprep.com/api/v3/earning_calendar?from=2026-05-01&to=2026-05-07&apikey={key}'),
    ('v3/economic_calendar (2026-05)',      f'https://financialmodelingprep.com/api/v3/economic_calendar?from=2026-05-01&to=2026-05-07&apikey={key}'),
    ('stable/earnings-calendar',           f'https://financialmodelingprep.com/stable/earnings-calendar?from=2026-05-01&to=2026-05-07&apikey={key}'),
    ('stable/economic-calendar',           f'https://financialmodelingprep.com/stable/economic-calendar?from=2026-05-01&to=2026-05-07&apikey={key}'),
    ('stable/profile?symbol=AAPL',        f'https://financialmodelingprep.com/stable/profile?symbol=AAPL&apikey={key}'),
]

for label, url in tests:
    try:
        r = urlopen(Request(url, headers={'User-Agent': 'test/1.0'}), timeout=10)
        data = json.loads(r.read())
        summary = f'len={len(data)}' if isinstance(data, list) else str(data)[:80]
        print(f'OK   {label}: {summary}')
    except Exception as e:
        print(f'FAIL {label}: {e}')
