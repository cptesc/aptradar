import sys
sys.stdout.reconfigure(encoding="utf-8")

from data.molit import fetch_trades

trades = fetch_trades("28710", "202401")

print(f"총 {len(trades)}건 조회됨\n")
for t in trades[:5]:
    print(t)
