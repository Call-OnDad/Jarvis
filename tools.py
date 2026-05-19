import python_weather
import asyncio
import assist
from icrawler.builtin import GoogleImageCrawler
import os
import spot
import requests
from dotenv import load_dotenv

load_dotenv()

OVERSEERR_URL = os.environ.get("OVERSEERR_URL", "http://localhost:5055")
OVERSEERR_API_KEY = os.environ.get("OVERSEERR_API_KEY", "")

def request_media(title):
    headers = {"X-Api-Key": OVERSEERR_API_KEY}
    try:
        search_resp = requests.get(
            f"{OVERSEERR_URL}/api/v1/search",
            params={"query": title},
            headers=headers,
            timeout=10,
        )
        search_resp.raise_for_status()
        results = search_resp.json().get("results", [])
        if not results:
            return f"Sorry Sir, I could not find {title} on Overseerr."

        match = results[0]
        media_type = match.get("mediaType")
        media_id = match.get("id")
        found_title = match.get("title") or match.get("name", title)

        body = {"mediaType": media_type, "mediaId": media_id}
        if media_type == "tv":
            body["seasons"] = "all"

        req_resp = requests.post(
            f"{OVERSEERR_URL}/api/v1/request",
            json=body,
            headers=headers,
            timeout=10,
        )
        if req_resp.status_code == 201:
            return f"Request submitted for {found_title}, Sir."
        elif req_resp.status_code == 409:
            return f"{found_title} has already been requested, Sir."
        else:
            return f"The request for {found_title} failed with status {req_resp.status_code}, Sir."
    except requests.RequestException as e:
        return f"Could not reach Overseerr, Sir. {e}"

async def get_weather(city_name):
    async with python_weather.Client(unit=python_weather.IMPERIAL) as client:
        weather = await client.get(city_name)
        return weather

def search(query):
    google_Crawler = GoogleImageCrawler(storage = {"root_dir": r'./images'})
    google_Crawler.crawl(keyword = query, max_num = 1)


def parse_command(command):
    if "weather" in command:
        weather_description = asyncio.run(get_weather("Chicago"))
        query = "System information: " + str(weather_description)
        print(query)
        response = assist.ask_question_memory(query)
        done = assist.TTS(response)

    if "search" in command:
        files = os.listdir("./images")
        [os.remove(os.path.join("./images", f))for f in files]
        query = command.split("-")[1]
        search(query)
    
    if "play" in command:
        spot.start_music()

    if "pause" in command:
        spot.stop_music()
    
    if "skip" in command:
        spot.skip_to_next()
    
    if "previous" in command:
        spot.skip_to_previous()
    
    if "spotify" in command:
        spotify_info = spot.get_current_playing_info()
        query = "System information: " + str(spotify_info)
        print(query)
        response = assist.ask_question_memory(query)
        done = assist.TTS(response)

    if "request" in command:
        title = command.split("-", 1)[1] if "-" in command else command
        result = request_media(title)
        print(result)
        assist.TTS(result)
        

    

        