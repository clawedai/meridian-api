# Meridian API (Backend for Drishti)

Backend API for the Drishti Intelligence Platform.

## Quick Start

### 1. Install Dependencies

```bash
cd meridian-api
pip install -r requirements.txt
```

### 2. Configure Environment

Edit `.env` with your credentials:

```env
# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
SUPABASE_SERVICE_KEY=your-service-role-key

# JWT Secret
SECRET_KEY=your-super-secret-key-min-32-chars-long

# Claude API (for AI analysis) - Optional but recommended
ANTHROPIC_API_KEY=sk-ant-...

# Resend (for email) - Optional
RESEND_API_KEY=re_...
FROM_EMAIL=Drishti <alerts@resend.dev>
```

### 3. Run Server

```bash
python main.py
```

Server: http://localhost:8000
API Docs: http://localhost:8000/docs

## API Endpoints

### Authentication
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/register` | Register new user |
| POST | `/api/v1/auth/login` | Login, get token |
| POST | `/api/v1/auth/logout` | Logout |
| GET | `/api/v1/auth/me` | Get current user |

### Entities
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/entities` | List entities |
| POST | `/api/v1/entities` | Create entity |
| GET | `/api/v1/entities/{id}` | Get entity |
| PATCH | `/api/v1/entities/{id}` | Update entity |
| DELETE | `/api/v1/entities/{id}` | Archive entity |

### Sources
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/sources` | List sources |
| POST | `/api/v1/sources` | Create source |
| GET | `/api/v1/sources/{id}` | Get source |
| PATCH | `/api/v1/sources/{id}` | Update source |
| DELETE | `/api/v1/sources/{id}` | Delete source |
| POST | `/api/v1/sources/{id}/refresh` | Trigger refresh |

### Insights
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/insights` | List insights |
| GET | `/api/v1/insights/{id}` | Get insight |
| PATCH | `/api/v1/insights/{id}` | Update insight |
| POST | `/api/v1/insights/{id}/mark-read` | Mark as read |
| POST | `/api/v1/insights/mark-all-read` | Mark all read |

### Pipeline (Intelligence Engine)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/pipeline/trigger` | Trigger data collection + AI analysis |
| GET | `/api/v1/pipeline/status` | Get pipeline status |

### Alerts
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/alerts` | List alerts |
| POST | `/api/v1/alerts` | Create alert |
| GET | `/api/v1/alerts/{id}` | Get alert |
| PATCH | `/api/v1/alerts/{id}` | Update alert |
| DELETE | `/api/v1/alerts/{id}` | Delete alert |

### Dashboard
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/dashboard/stats` | Dashboard statistics |
| GET | `/api/v1/dashboard/recent-insights` | Recent insights |

## Intelligence Pipeline

The pipeline automatically:
1. **Collects** data from RSS feeds, webpages, and APIs
2. **Analyzes** content using Claude AI (or keyword fallback)
3. **Generates** structured insights with importance ratings
4. **Stores** everything in Supabase

### Trigger Pipeline

```bash
# Process all entities
curl -X POST http://localhost:8000/api/v1/pipeline/trigger \
  -H "Authorization: Bearer <token>"

# Process single entity
curl -X POST http://localhost:8000/api/v1/pipeline/trigger \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "uuid-here"}'
```

## Project Structure

```
meridian-api/
├── main.py                 # FastAPI entry point
├── requirements.txt        # Dependencies
├── .env                   # Config (create from .env.example)
├── app/
│   ├── api/
│   │   ├── deps.py       # Auth dependencies
│   │   └── v1/
│   │       ├── auth.py    # Authentication
│   │       ├── entities.py
│   │       ├── sources.py
│   │       ├── insights.py
│   │       ├── alerts.py
│   │       ├── reports.py
│   │       ├── dashboard.py
│   │       └── pipeline.py  # Intelligence engine
│   ├── core/
│   │   ├── config.py     # Settings
│   │   └── security.py    # JWT utilities
│   ├── db/
│   │   └── database.py   # DB connection (legacy)
│   ├── schemas/          # Pydantic models
│   └── services/
│       ├── pipeline.py    # Data collection + AI
│       ├── processor.py   # Background jobs
│       └── notifier.py   # Email/Webhooks
└── README.md
```

## Deployment

Recommended platforms:
- **Railway** (easiest for Python)
- **Render**
- **Fly.io**
- **AWS Lambda** (with Mangum)

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_KEY` | Yes | Supabase anon key |
| `SUPABASE_SERVICE_KEY` | Yes | Supabase service role key |
| `SECRET_KEY` | Yes | JWT signing key |
| `ANTHROPIC_API_KEY` | No | Claude API for AI analysis |
| `RESEND_API_KEY` | No | Email notifications |
| `FROM_EMAIL` | No | Sender email |
