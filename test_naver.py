import sys
import asyncio
sys.stdout.reconfigure(encoding="utf-8")

from crawler.naver import fetch_listings

async def main():
    print("인천 서구 84㎡ 매물 조회 중 (단지 3개 제한)...\n")
    listings = await fetch_listings("인천 서구", 84, limit=3)

    print(f"\n총 {len(listings)}건 조회됨\n")
    for item in listings[:3]:
        print(item)

asyncio.run(main())
