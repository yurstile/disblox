# Disblox API

A FastAPI-based backend service for Discord bot management and Roblox account verification integration.

## Prerequisites

- Python 3.8 or higher
- MySQL 8.0 or higher
- Discord Developer Account
- Roblox Developer Account

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yurstile/disblox.git
cd disblox
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

## Configuration

1. Copy the environment template:
```bash
cp env.example .env
```

2. Configure the following environment variables in `.env`:

### Discord Configuration
- `DISCORD_TOKEN`: Your Discord bot token
- `DISCORD_APPLICATION_ID`: Discord application ID
- `DISCORD_CLIENT_ID`: Discord OAuth2 client ID
- `DISCORD_CLIENT_SECRET`: Discord OAuth2 client secret
- `DISCORD_REDIRECT_URI`: Discord OAuth2 redirect URI

### Roblox Configuration
- `ROBLOX_CLIENT_ID`: Roblox OAuth2 client ID
- `ROBLOX_CLIENT_SECRET`: Roblox OAuth2 client secret
- `ROBLOX_REDIRECT_URI`: Roblox OAuth2 redirect URI

### API Configuration
- `API_HOST`: API server host (default: 0.0.0.0)
- `API_PORT`: API server port (default: 8000)
- `JWT_SECRET_KEY`: Secret key for JWT token generation

### Database Configuration
- `MYSQL_HOST`: MySQL server host
- `MYSQL_PORT`: MySQL server port (default: 3306)
- `MYSQL_USER`: MySQL username
- `MYSQL_PASSWORD`: MySQL password
- `MYSQL_DATABASE`: MySQL database name

## Database Setup

1. Create a MySQL database:
```sql
CREATE DATABASE disblox_api;
```

2. The application will automatically create tables on startup using SQLAlchemy models.

## Running the Application

Start the server:
```bash
python server.py
```

The application will start both the FastAPI server and Discord bot (if configured).

## API Endpoints

- **Authentication**: `/auth/*` - User authentication and Discord OAuth
- **Dashboard**: `/dashboard/*` - User dashboard and server management
- **Roblox**: `/roblox/*` - Roblox account linking and verification
- **Server**: `/server/*` - Server configuration and management

## Development

The project uses:
- FastAPI for the web framework
- SQLAlchemy for database ORM
- Discord.py for bot functionality
- JWT for authentication
- MySQL for data persistence

## Security Features

- Rate limiting (60 requests/minute, 1000 requests/hour)
- Content Security Policy (CSP) headers
- Security headers (XSS protection, frame options, etc.)
- JWT-based authentication
- CORS configuration

## License

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0).

**IMPORTANT COMPLIANCE REQUIREMENTS:**

- **Source Code Disclosure**: If you run this software over a network, you MUST provide the complete source code to all users who interact with it.
- **Network Use**: Any use of this software over a network (including web applications) triggers the AGPL v3 copyleft provisions.
- **Derivative Works**: Any modifications or derivative works must also be licensed under AGPL v3.
- **Source Distribution**: You must provide the complete source code when distributing or running modified versions.

**Full License Text**: See [LICENSE](LICENSE) file for complete terms and conditions.

**Compliance is mandatory. Failure to comply may result in license termination.**
