# DJ Showcase

A portfolio demonstration that uses GPT to craft mood descriptions from keywords, then creates real Spotify playlists with a step-by-step animated reveal of the AI pipeline.

## How It Works

1. **Choose a Mood** — Select from curated presets or enter your own keywords
2. **Watch the Magic** — See each step of the pipeline animate in real-time:
   - Keywords are analysed
   - GPT crafts a mood description
   - GPT selects tracks that match the mood
   - Spotify playlist is created live
   - Email with playlist link (optional)
3. **Enjoy Your Playlist** — Open directly in Spotify

## Tech Stack

- **Backend**: [Litestar](https://litestar.dev/) (Python async web framework)
- **Frontend**: HTMX + Jinja2 templates + TailwindCSS
- **AI**: OpenAI GPT-3.5/4 for mood and playlist generation
- **Music**: Spotify Web API for playlist creation
- **Email**: Resend for sending playlist links (optional)

## Prerequisites

You will need accounts for:

1. **[OpenAI](https://platform.openai.com/)** — GPT generates mood descriptions and playlist recommendations
2. **[Spotify Developer](https://developer.spotify.com/)** — Create and populate playlists via the Web API
3. **[Resend](https://resend.com/)** (Optional) — Send playlist links via email

## Installation

```bash
# Install Python dependencies
pip install -r requirements.txt

# Build Tailwind CSS (if modifying styles)
tailwindcss -i ./styles/main.css -o ./static/css/main.css --watch
```

## Environment Variables

Create a `.env` file with the following variables:

```bash
# OpenAI
OPENAI_API_KEY=sk-...

# Spotify
SPOTIPY_CLIENT_ID=your_client_id
SPOTIPY_CLIENT_SECRET=your_client_secret
SPOTIFY_USER=your_spotify_username

# Resend (optional — email will be skipped if not set)
RESEND_API_KEY=re_...
```

See `.env.example` for a template.

## Usage

### Quick Start (with HTTPS)

Using [Caddy](https://caddyserver.com/) for local HTTPS eliminates Safari's "HTTPS-Only" errors:

```bash
# Install Caddy (macOS)
brew install caddy

# Trust Caddy's local CA (one-time, requires sudo)
caddy trust

# Terminal 1: Start the backend
litestar run --debug

# Terminal 2: Start Caddy reverse proxy
caddy run
```

Navigate to `https://localhost` to open the showcase.

### Without Caddy

If you don't need HTTPS (or use Chrome/Firefox without strict HTTPS mode):

```bash
litestar run --debug
```

Navigate to `http://127.0.0.1:8000` to open the showcase.

> **Note**: Safari with "HTTPS-Only" mode enabled will not load HTTP URLs. Use the Caddy setup above or disable HTTPS-Only in Safari settings.

### First Run: Spotify Authentication

On first run, you'll be redirected to Spotify to authenticate. This grants the app permission to create playlists on your behalf. The OAuth tokens are cached locally for subsequent runs.

**Important**: In your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), add `https://localhost/callback` as a redirect URI in your app settings.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/dj` | GET | Main showcase page |
| `/presets` | GET | List of available mood presets |
| `/generate` | POST | Start playlist generation (returns request_id) |
| `/generate/{request_id}/status` | GET | SSE stream of generation progress |
| `/playlist` | POST | Legacy endpoint for direct playlist generation |

## Presets

The app includes 5 curated mood presets:

| Preset | Keywords |
|--------|----------|
| Morning Coffee | calm, acoustic, sunrise, hopeful |
| Workout Beast | powerful, intense, driving, unstoppable |
| Rainy Afternoon | melancholy, piano, thoughtful, rain |
| Summer Road Trip | sunny, freedom, nostalgic, adventure |
| Late Night Focus | minimal, electronic, ambient, focus |

## Architecture

```
User Input (Preset/Custom) → GPT Mood → GPT Playlist → Spotify API → Resend Email
       ↓                        ↓            ↓              ↓
   [UI Step 1]            [UI Step 2]  [UI Step 3]    [UI Step 4]
```

The frontend uses Server-Sent Events (SSE) to stream progress updates from the backend, creating a real-time animated reveal of each step in the pipeline.

## Project Structure

```
klaviyo-dj/
├── app.py              # Main application and route handlers
├── models.py           # Pydantic models (Preset, PlaylistRequest)
├── requirements.txt    # Python dependencies
├── Caddyfile           # Caddy reverse proxy config for local HTTPS
├── templates/
│   ├── base.html       # Base template with head/scripts
│   └── poster.html     # Main showcase page
├── static/
│   ├── css/            # Compiled Tailwind CSS
│   ├── images/         # Icons and assets
│   └── scripts/        # HTMX and utility scripts
└── styles/
    └── main.css        # Tailwind source
```
