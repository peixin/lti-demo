# LTI 1.3 Platform (LMS)

[简体中文](README.cn.md)

---

This directory contains the LTI 1.3 Platform (LMS) service, designed using Flask and SQLite. It plays the role of a Learning Management System (LMS), managing users, courses, assignments, and registering LTI tools.

### ⚙️ Containerization Details
This service is fully containerized as an independent project:
- **Environment Control**: Manage configurations via local `.env`.
- **Database Persistence**: SQLite database maps to host `./data` directory (resolves to `/app/data/platform.db` in container).
- **Executable Builds**: Easily build images locally using `./build.sh`.

### 🚀 Setup and Launch

**1. Configure Environment Variables**
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
*You can customize `PORT` (default: 8001) or `SECRET_KEY` inside `.env`.*

**2. Build the Docker Image**
You can build the image directly using the custom build script (it tags the image as `beiming/lti-platform:latest`):
```bash
./build.sh
```
*(Alternatively, run `docker compose build`)*

**3. Start the Container**
Start the container in the background:
```bash
docker compose up -d
```

**4. Open in Browser**
Visit the Platform dashboard:
* URL: `http://localhost:8001` (or the custom `PORT` you configured).
