# LTI 1.3 Demo

[简体中文](README.cn.md)

---

A minimal, end-to-end self-contained implementation of the **LTI 1.3** (Learning Tools Interoperability) standard, featuring two completely independent services that communicate over the OIDC launch flow with grade passback via AGS.

### ✨ Core Highlights & Promotional Features
- **100% Raw Reference Code**: Written in plain Python Flask with raw SQLite queries and zero heavy ORMs, making it the ultimate educational sandbox to understand the core handshake mechanics of LTI 1.3.
- **Enterprise-Grade Security Sandbox**: Real asymmetric RSA-2048 keypair generation (JWKS), real RS256 token signing (PyJWT), OIDC form_post launching, and full JWT claims validation.
- **Multi-Language (i18n) Support**: Fully localized in English, Chinese (Simplified & Traditional), and Korean out of the box, with automatic browser language detection and instant switchers.
- **Aesthetic Bulma UI**: Powered by Jinja2 and Bulma CSS via CDN with subtle modern micro-animations, providing a high-premium developer UI without any heavy node build steps.

### 🛡️ LTI 1.3 Specs & Protocols Supported
This project supports the complete core LTI 1.3 specification suite:
- **OIDC Core Launch (LtiResourceLinkRequest)**: Handles secure authentication and single-sign-on launch flows for student exam attempts.
- **Deep Linking 2.0 (LtiDeepLinkingRequest / Response)**: Enables instructors to securely open the Tool's course selector page and select specific exam categories, posting links and parameters back to the Platform.
- **Assignment and Grade Services (AGS 2.0)**: Supports synchronous column mapping (Lineitems), score submission with idempotency protection, and grades read-back (Results).
- **Names and Role Provisioning Services (NRPS)**: Dynamically fetches class rosters and memberships from the LMS Platform database.
- **Submission Review (LtiSubmissionReviewRequest)**: Safely redirects instructors from the LMS gradebook directly to a read-only review of a student's specific attempt on the Tool.

### 🧬 Custom Extensions & Claims
We implemented real-world production claim overrides:
- **`custom.category`**: Route custom question categories securely based on LTI parameter bindings.
- **`custom.tool_event_id` / `submissionId`**: Custom idempotency variables linked to specific exam attempts to enable multi-level fallback submission reviews.
- **`answering_duration`**: Measures and displays the exact duration taken by the student to finish the exam.

### 📂 Standalone Decoupled Dockerization
Both services have been fully containerized and decoupled as **two separate standalone projects** running Flask on Python 3.12 with SQLite databases:
- **Independent Environments**: Managed by local `.env` files.
- **Persistent SQLite Mappings**: Host directories are mounted under `./data/` for both projects to store `platform.db` and `exam-tool.db` robustly.
- **Local Building**: Custom scripts (`build.sh`) are provided to compile local images natively.

---

### 🚀 Quick Start Guides
For detailed setup and execution instructions of each individual service, please refer to their respective READMEs:

* 🏫 **LMS Platform Service**: Go to [platform/README.md](platform/README.md) to manage users, courses, and tool registrations.
* 📝 **Exam Tool Service**: Go to [exam-tool/README.md](exam-tool/README.md) to manage exam deliveries and LTI launch payloads.
