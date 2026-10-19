import httpx
import asyncio

async def main():
  async with httpx.AsyncClient() as client:
    resp = await client.get("https://oit-ead-canvas-syllabus.s3.amazonaws.com/uta.instructure.com/2026-FALL/281409-2268-ME-6304-001/2026-FALL_2268-ME-6304-001.pdf")

    if resp.status_code == 200:
      print("File size in bytes: ", len(resp.content))
      print(type(resp.content))
      print(type(resp.text))

    with open("downloaded.pdf", "wb") as f:
      f.write(resp.content)

asyncio.run(main())