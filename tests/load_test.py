"""
Paradox-DB Load Test
Simulates 100 concurrent users hitting the gateway.
Run: python tests/load_test.py --url http://localhost:8000
"""
import asyncio
import time
import argparse
import statistics
import httpx


class LoadTest:
    def __init__(self, base_url: str, num_users: int = 100):
        self.base_url = base_url.rstrip("/")
        self.num_users = num_users
        self.results: list[float] = []
        self.errors: int = 0

    async def _register(self, client: httpx.AsyncClient) -> str | None:
        try:
            r = await client.post(f"{self.base_url}/v1/auth/register", timeout=10)
            r.raise_for_status()
            return r.json()["api_key"]
        except Exception:
            self.errors += 1
            return None

    async def _upload(self, client: httpx.AsyncClient, api_key: str, user_idx: int):
        import base64
        data = base64.b64encode(b"\x00" * 1024).decode()
        payload = {
            "database_name": f"loadtest_{user_idx}.db",
            "file_data": data,
        }
        start = time.time()
        try:
            r = await client.post(
                f"{self.base_url}/v1/upload",
                json=payload,
                headers={"X-API-Key": api_key},
                timeout=30,
            )
            elapsed = (time.time() - start) * 1000
            if r.status_code == 200:
                self.results.append(elapsed)
            else:
                self.errors += 1
        except Exception:
            self.errors += 1

    async def _download(self, client: httpx.AsyncClient, api_key: str, user_idx: int):
        start = time.time()
        try:
            r = await client.get(
                f"{self.base_url}/v1/download",
                params={"database_name": f"loadtest_{user_idx}.db"},
                headers={"X-API-Key": api_key},
                timeout=30,
            )
            elapsed = (time.time() - start) * 1000
            if r.status_code in (200, 404):
                self.results.append(elapsed)
            else:
                self.errors += 1
        except Exception:
            self.errors += 1

    async def run_upload_test(self):
        print(f"\n--- Upload Test: {self.num_users} concurrent users ---")
        self.results.clear()
        self.errors = 0

        async with httpx.AsyncClient() as client:
            # Register users
            print("Registering users...")
            api_keys = await asyncio.gather(
                *[self._register(client) for _ in range(self.num_users)]
            )
            api_keys = [k for k in api_keys if k]
            print(f"Registered {len(api_keys)} users")

            # Concurrent uploads
            start = time.time()
            await asyncio.gather(
                *[self._upload(client, key, i) for i, key in enumerate(api_keys)]
            )
            wall_time = time.time() - start

            self._report(wall_time, "Upload")

    async def run_download_test(self):
        print(f"\n--- Download Test: {self.num_users} concurrent users ---")
        self.results.clear()
        self.errors = 0

        async with httpx.AsyncClient() as client:
            api_keys = await asyncio.gather(
                *[self._register(client) for _ in range(self.num_users)]
            )
            api_keys = [k for k in api_keys if k]

            start = time.time()
            await asyncio.gather(
                *[self._download(client, key, i) for i, key in enumerate(api_keys)]
            )
            wall_time = time.time() - start

            self._report(wall_time, "Download")

    async def run_mixed_test(self):
        half = self.num_users // 2
        print(f"\n--- Mixed Test: {half} uploads + {half} downloads ---")
        self.results.clear()
        self.errors = 0

        async with httpx.AsyncClient() as client:
            api_keys = await asyncio.gather(
                *[self._register(client) for _ in range(self.num_users)]
            )
            api_keys = [k for k in api_keys if k]

            start = time.time()
            await asyncio.gather(
                *[self._upload(client, key, i) for i, key in enumerate(api_keys[:half])],
                *[self._download(client, key, i) for i, key in enumerate(api_keys[half:])],
            )
            wall_time = time.time() - start

            self._report(wall_time, "Mixed")

    def _report(self, wall_time: float, label: str):
        success = len(self.results)
        print(f"\nResults ({label}):")
        print(f"  Total requests: {success + self.errors}")
        print(f"  Success: {success}")
        print(f"  Errors: {self.errors}")
        print(f"  Wall time: {wall_time:.2f}s")

        if self.results:
            p50 = statistics.median(self.results)
            p95 = sorted(self.results)[int(len(self.results) * 0.95)]
            p99 = sorted(self.results)[int(len(self.results) * 0.99)]
            print(f"  Latency p50: {p50:.0f}ms")
            print(f"  Latency p95: {p95:.0f}ms")
            print(f"  Latency p99: {p99:.0f}ms")

            # Pass/fail
            if self.errors == 0 and p95 < 5000:
                print(f"  PASS: No errors, p95 < 5s")
            else:
                print(f"  FAIL: errors={self.errors}, p95={p95:.0f}ms")
        else:
            print(f"  FAIL: No successful requests")

    async def run_all(self):
        await self.run_upload_test()
        await self.run_download_test()
        await self.run_mixed_test()


def main():
    parser = argparse.ArgumentParser(description="Paradox-DB Load Test")
    parser.add_argument("--url", default="http://localhost:8000", help="Gateway URL")
    parser.add_argument("--users", type=int, default=100, help="Number of concurrent users")
    args = parser.parse_args()

    lt = LoadTest(args.url, args.users)
    asyncio.run(lt.run_all())


if __name__ == "__main__":
    main()
