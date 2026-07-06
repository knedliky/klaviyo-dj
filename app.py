import asyncio
import json
import logging
import uuid
from os import environ
from pathlib import Path
from typing import AsyncGenerator

import resend
import spotipy
from litestar import Litestar, get, post
from litestar.contrib.htmx.request import HTMXRequest
from litestar.contrib.htmx.response import HTMXTemplate
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.openapi.config import OpenAPIConfig
from litestar.response import Stream, Template
from litestar.static_files.config import StaticFilesConfig
from litestar.template.config import TemplateConfig
from openai import OpenAI
from pydantic import EmailStr
from spotipy.oauth2 import SpotifyOAuth

from models import Preset

logger = logging.getLogger(__name__)

#################
### Presets #####
#################

PRESETS: list[Preset] = [
    Preset(
        id="morning-coffee",
        name="Morning Coffee",
        description="Gentle wake-up vibes",
        keywords=["calm", "acoustic", "sunrise", "hopeful"],
        icon="☕",
    ),
    Preset(
        id="workout-beast",
        name="Workout Beast",
        description="High-energy gym motivation",
        keywords=["powerful", "intense", "driving", "unstoppable"],
        icon="💪",
    ),
    Preset(
        id="rainy-afternoon",
        name="Rainy Afternoon",
        description="Cosy introspective mood",
        keywords=["melancholy", "piano", "thoughtful", "rain"],
        icon="🌧️",
    ),
    Preset(
        id="summer-road-trip",
        name="Summer Road Trip",
        description="Windows down, music up",
        keywords=["sunny", "freedom", "nostalgic", "adventure"],
        icon="🚗",
    ),
    Preset(
        id="late-night-focus",
        name="Late Night Focus",
        description="Deep work concentration",
        keywords=["minimal", "electronic", "ambient", "focus"],
        icon="🌙",
    ),
]

# Lazy client initialization — allows app to start without all env vars
_openai_client: OpenAI | None = None
_spotify_client: spotipy.Spotify | None = None


def get_openai_client() -> OpenAI:
    """Lazily initialise OpenAI client on first use."""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


def get_spotify_client() -> spotipy.Spotify:
    """Lazily initialise Spotify client on first use."""
    global _spotify_client
    if _spotify_client is None:
        _spotify_client = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=environ["SPOTIPY_CLIENT_ID"],
                client_secret=environ["SPOTIPY_CLIENT_SECRET"],
                redirect_uri="https://localhost/callback",
                scope="playlist-modify-public",
            )
        )
    return _spotify_client


# Resend API key (optional — email will be skipped if not set)
resend.api_key = environ.get("RESEND_API_KEY", "")

# In-memory store for generation requests (simple demo — would use Redis in production)
GENERATION_REQUESTS: dict[str, dict] = {}

#################
### Functions ###
#################


# Extract mood keywords from a natural language sentence
async def gpt_extract_moods(sentence: str) -> list[str]:
    """
    Analyse a natural language sentence describing a situation, activity, or feeling
    and extract relevant mood keywords for playlist generation.
    """
    completion = get_openai_client().chat.completions.create(
        model="gpt-3.5-turbo",
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": """You are a music mood analyst. Given a sentence describing a situation,
activity, or feeling, extract 4-6 mood keywords that would be appropriate for a music playlist.

Consider:
- The activity or situation described (cooking, driving, working, relaxing)
- The time of day or setting implied
- The emotional tone (happy, melancholic, energetic, peaceful)
- The social context (alone, with friends, romantic)
- Musical attributes that would fit (upbeat, acoustic, electronic, chill)

Return a JSON object with a single key "moods" containing an array of keyword strings.
Example: {"moods": ["warm", "social", "acoustic", "relaxed", "Friday night"]}""",
            },
            {"role": "user", "content": sentence},
        ],
    )
    result = json.loads(completion.choices[0].message.content)
    return result.get("moods", [])


# Get ChatGPT output for Mood
async def gpt_mood(keywords: list[str]) -> str:
    completion = get_openai_client().chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {
                "role": "system",
                "content": "You are a poetic assistant, \
                skilled in writing concise yet emotional prose. \
                Your role is to write a single sentence. \
                You will have a set of keywords to create your concise, \
                descriptive sentence. Based on the keywords, describe a mood. \
                Do not use the word mood or genre. \
                The description will be used to describe a music playlist for someone you care about.",
            },
            {"role": "user", "content": str(keywords)},
        ],
    )
    return completion.choices[0].message.content


# Get ChatGPT output for Playlist in JSON format
async def gpt_playlist(description: str) -> dict:
    response = get_openai_client().chat.completions.create(
        model="gpt-4-1106-preview",
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant designed to output perfectly formatted JSON. \
                You role is to return a list of song names and song artists, as well as a playlist title. \
                You will have a description to create your list. \
                Based on the description, create a list of songs. \
                Do not use the word song or artist. \
                The list will be used for a music playlist to someone you care about. \
                Do not use a list you have already used. \
                There should be a key for the playlist_title and tracks. \
                Tracks should be a list of song titles and artist.",
            },
            {
                "role": "assistant",
                "content": 'Are you sure that it is valid? Make sure there is a key for playlist_title and tracks. \
                tracks is a list of song and artist. \
                It should look like this: \
                {"playlist_title": "My Playlist", \
                "tracks": [{"song": "Song Name", "artist": "Artist Name"}, {"song": "Song Name", "artist": "Artist Name"}]}',
            },
            {
                "role": "user",
                "content": description,
            },
        ],
    )

    return {
        **json.loads(response.choices[0].message.content),
        "description": description,
    }


# Initialise Spotify playlist and return the playlist JSON object
def initialise_playlist(user: str, title: str, description: str) -> dict:
    return get_spotify_client().user_playlist_create(user, title, description=description)


# Search and add tracks to playlist to a users playlist (One by one)
async def add_track_to_playlist(user: str, track: dict, playlist_id: str):
    # Search for most similar track and artist, to minimize hallucinations
    artist = track["artist"]
    title = track["title"]
    spotify = get_spotify_client()
    spotify_track = spotify.search(
        q=f"artist:{artist} track:{title}", limit=1, type="track"
    )

    try:
        # Add track to playlist
        track_id = spotify_track["tracks"]["items"][0]["id"]
        spotify.user_playlist_add_tracks(user, playlist_id, [track_id])
    except IndexError:
        # Handle the IndexError gracefully
        logging.error(f"Could not find track: {track} by {artist}in Spotify")
        # You can choose to skip adding the track or take any other appropriate action
    logging.info(f"Added track: {track} by {artist} to playlist")


# Create full Spotify playlist from GPT JSON, returning a URL link for the playlist
async def create_spotify_playlist(
    user: str, gpt_playlist: dict
) -> tuple[str, str, str]:
    # Initialise the playlist
    title = gpt_playlist["playlist_title"]
    description = gpt_playlist["description"]
    playlist = initialise_playlist(user, title, description)

    # For every song recommended by GPT, search and add to Spotify playlist
    for song in gpt_playlist["tracks"]:
        await add_track_to_playlist(user, song, playlist["id"])

    # Finally, get playlist link
    title = playlist["name"]
    url = playlist["external_urls"]["spotify"]
    description = playlist["description"]

    return title, description, url


# Send playlist email via Resend
async def send_playlist_email(email: str, title: str, url: str, description: str) -> bool:
    """Send the playlist link to the user via Resend. Returns True if sent successfully."""
    if not resend.api_key:
        logger.warning("RESEND_API_KEY not set — skipping email")
        return False

    try:
        resend.Emails.send(
            {
                "from": "DJ <dj@resend.dev>",
                "to": email,
                "subject": f"Your playlist: {title}",
                "html": f"""
                <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 40px 20px;">
                    <h1 style="color: #1DB954; margin-bottom: 24px;">🎵 Your Playlist is Ready!</h1>
                    <p style="font-size: 18px; color: #333; margin-bottom: 16px;">{description}</p>
                    <a href="{url}" style="display: inline-block; background-color: #1DB954; color: white; padding: 16px 32px; text-decoration: none; border-radius: 50px; font-weight: 600; font-size: 16px;">
                        Open "{title}" on Spotify
                    </a>
                    <p style="color: #666; font-size: 14px; margin-top: 32px;">
                        Created with GPT + Spotify by DJ Showcase
                    </p>
                </div>
                """,
            }
        )
        logger.info(f"Email sent to {email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


# Helper to get preset by ID
def get_preset_by_id(preset_id: str) -> Preset | None:
    """Find a preset by its ID."""
    for preset in PRESETS:
        if preset.id == preset_id:
            return preset
    return None


#################
### Endpoints ###
#################


# Index route — main showcase page
@get("/")
async def index() -> Template:
    logger.info("Returning DJ Showcase page")
    return Template(template_name="poster.html", context={"presets": PRESETS})


# Return list of available presets
@get("/presets")
async def get_presets() -> list[dict]:
    logger.info("Returning presets list")
    return [preset.dict() for preset in PRESETS]


# Route to receive keywords and return a recommended playlist from GPT
@post("/playlist")
async def playlist(request: HTMXRequest) -> Template:
    form_data = await request.form()
    keywords = form_data.get("keywords")
    description = await gpt_mood(keywords)
    playlist = await gpt_playlist(description)
    logger.info(f"GPT mood created: {description}\nGPT playlist created: {playlist}")
    context = {"keywords": keywords, "description": description, "playlist": playlist}
    return HTMXTemplate(
        template_name="partials/table_gpt_playlist.html",
        context={"playlist": context},
    )


# Start playlist generation and return request ID
@post("/generate")
async def start_generation(request: HTMXRequest) -> dict:
    """
    Start the playlist generation workflow.
    Accepts either a preset_id or a natural language sentence, plus an optional email.
    Returns a request_id to subscribe to SSE updates.
    """
    form_data = await request.form()
    email = form_data.get("email")
    preset_id = form_data.get("preset_id")
    sentence = form_data.get("sentence")

    # Determine input source and store appropriately
    if preset_id:
        preset = get_preset_by_id(preset_id)
        if not preset:
            return {"error": f"Preset '{preset_id}' not found"}
        keywords = preset.keywords
        source = "preset"
        original_input = preset.name
    elif sentence:
        # Sentence will be processed in SSE to extract moods
        keywords = None  # Will be extracted during generation
        source = "sentence"
        original_input = sentence.strip()
    else:
        return {"error": "Please describe what you're in the mood for, or select a preset"}

    # Create request ID and store request data
    request_id = str(uuid.uuid4())
    GENERATION_REQUESTS[request_id] = {
        "email": email,
        "keywords": keywords,
        "sentence": sentence.strip() if sentence else None,
        "original_input": original_input,
        "source": source,
        "status": "pending",
    }

    logger.info(f"Generation request {request_id} created: source={source}, input={original_input}")
    return {"request_id": request_id}


# SSE endpoint for streaming generation progress
@get("/generate/{request_id:str}/status")
async def generation_status(request_id: str) -> Stream:
    """
    Server-Sent Events endpoint for streaming the playlist generation workflow.
    Emits progress updates at each step of the pipeline.
    """

    async def event_generator() -> AsyncGenerator[bytes, None]:
        # Retrieve request data
        request_data = GENERATION_REQUESTS.get(request_id)
        if not request_data:
            yield f"data: {json.dumps({'step': 0, 'status': 'error', 'message': 'Request not found'})}\n\n".encode()
            return

        keywords = request_data["keywords"]
        sentence = request_data.get("sentence")
        source = request_data["source"]
        original_input = request_data["original_input"]
        email = request_data["email"]

        try:
            # Step 1: Analyse input and extract moods (if sentence-based)
            if source == "sentence":
                yield f"data: {json.dumps({'step': 1, 'status': 'processing', 'message': 'Understanding your vibe...', 'data': {'sentence': original_input}})}\n\n".encode()
                keywords = await gpt_extract_moods(sentence)
                yield f"data: {json.dumps({'step': 1, 'status': 'complete', 'message': 'Vibe understood', 'data': {'sentence': original_input, 'keywords': keywords}})}\n\n".encode()
            else:
                # Preset-based: keywords already known
                yield f"data: {json.dumps({'step': 1, 'status': 'processing', 'message': 'Analysing your mood...', 'data': {'keywords': keywords}})}\n\n".encode()
                await asyncio.sleep(0.5)  # Brief pause for UI effect
                yield f"data: {json.dumps({'step': 1, 'status': 'complete', 'message': 'Mood analysed', 'data': {'keywords': keywords}})}\n\n".encode()

            # Step 2: Generate mood description
            yield f"data: {json.dumps({'step': 2, 'status': 'processing', 'message': 'Crafting your mood...'})}\n\n".encode()
            mood = await gpt_mood(keywords)
            yield f"data: {json.dumps({'step': 2, 'status': 'complete', 'message': 'Mood crafted', 'data': {'mood': mood}})}\n\n".encode()

            # Step 3: Generate playlist
            yield f"data: {json.dumps({'step': 3, 'status': 'processing', 'message': 'Selecting tracks...'})}\n\n".encode()
            playlist_data = await gpt_playlist(mood)
            tracks = playlist_data.get("tracks", [])
            yield f"data: {json.dumps({'step': 3, 'status': 'complete', 'message': f'{len(tracks)} tracks selected', 'data': {'playlist': playlist_data}})}\n\n".encode()

            # Step 4: Create Spotify playlist
            yield f"data: {json.dumps({'step': 4, 'status': 'processing', 'message': 'Creating Spotify playlist...'})}\n\n".encode()
            title, description, url = await create_spotify_playlist(
                user=environ.get("SPOTIFY_USER"), gpt_playlist=playlist_data
            )
            yield f"data: {json.dumps({'step': 4, 'status': 'complete', 'message': 'Playlist live!', 'data': {'title': title, 'url': url, 'description': description}})}\n\n".encode()

            # Step 5: Send email
            if email:
                yield f"data: {json.dumps({'step': 5, 'status': 'processing', 'message': 'Sending email...'})}\n\n".encode()
                email_sent = await send_playlist_email(email, title, url, description)
                if email_sent:
                    yield f"data: {json.dumps({'step': 5, 'status': 'complete', 'message': 'Email on its way!'})}\n\n".encode()
                else:
                    yield f"data: {json.dumps({'step': 5, 'status': 'skipped', 'message': 'Email skipped (no API key)'})}\n\n".encode()
            else:
                yield f"data: {json.dumps({'step': 5, 'status': 'skipped', 'message': 'No email provided'})}\n\n".encode()

            # Final done event
            yield f"data: {json.dumps({'step': 6, 'status': 'done', 'message': 'All done!', 'data': {'title': title, 'url': url}})}\n\n".encode()

            # Clean up request
            GENERATION_REQUESTS[request_id]["status"] = "complete"

        except Exception as e:
            logger.error(f"Generation error: {e}")
            yield f"data: {json.dumps({'step': 0, 'status': 'error', 'message': str(e)})}\n\n".encode()
            GENERATION_REQUESTS[request_id]["status"] = "error"

    return Stream(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


app = Litestar(
    route_handlers=[
        index,
        get_presets,
        playlist,
        start_generation,
        generation_status,
    ],
    request_class=HTMXRequest,
    openapi_config=OpenAPIConfig(
        title="Demo API",
        version="0.1.0",
    ),
    template_config=TemplateConfig(
        directory=Path("templates"), engine=JinjaTemplateEngine
    ),
    static_files_config=[
        StaticFilesConfig(directories=["static"], path="/static", name="static")
    ],
)
