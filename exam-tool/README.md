# LTI 1.3 Exam Tool (Tool)

[简体中文](README.cn.md)

---

This directory contains the LTI 1.3 Exam Tool (LTI Tool) service, built using Flask and SQLite. It delivers online quizzes and exams, handles LTI 1.3 launch security payloads, and posts student scores back to the LMS platform via AGS.

### ⚙️ Containerization Details
This service is fully containerized as an independent project:
- **Environment Control**: Manage configurations via local `.env`.
- **Database Persistence**: SQLite database maps to host `./data` directory (resolves to `/app/data/exam-tool.db` in container).
- **Executable Builds**: Easily build images locally using `./build.sh`.

### 🚀 Setup and Launch

**1. Configure Environment Variables**
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
*You can customize `PORT` (default: 8002), `SECRET_KEY`, or the admin panel password `ADMIN_PASSWORD` inside `.env`.*

**2. Build the Docker Image**
You can build the image directly using the custom build script (it tags the image as `beiming/lti-exam-tool:latest`):
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
Visit the Exam Tool interface or Admin panel:
* Client/Student Link: `http://localhost:8002` (requires LTI Launch from Platform to run exams).
* Administrative Control Panel: `http://localhost:8002/admin` (Default password: `admin` or what you configured in `.env`).
