1. Open the project

cd "C:\Users\lenovo\Documents\Resume Projects\Apply"

2. Start/confirm Docker services
docker compose -f infrastructure/docker/docker-compose.yml up -d
docker compose -f infrastructure/docker/docker-

4. Run the backend — Terminal 1

cd "C:\Users\lenovo\Documents\Resume Projects\Apply"
.\backend\venv\Scripts\Activate.ps1
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --reload

Check it:
curl.exe http://localhost:8000/api/v1/health/ready


7. Run the extension — Terminal 4

cd "C:\Users\lenovo\Documents\Resume Projects\Apply\apps\extension"
npm run dev

In Chrome:
i. Open chrome://extensions
ii. Enable Developer mode
iii. Click Load unpacked
iv. Select:

C:\Users\lenovo\Documents\Resume Projects\Apply\apps\extension\build\chrome-mv3-dev

v. Pin the extension.
Finally, reload any job-application tab opened before the extension was loaded, then use the extension’s Autofill workflow. You should sign in to the website before opening the extension popup.


8.Run this in a dedicated terminal and leave it open:

cd "C:\Users\lenovo\Documents\Resume Projects\Apply\apps\extension"
npm run dev

Then open chrome://extensions and click the extension’s Reload icon. The ws://localhost:1815 / 1816 errors should disappear. Those ports are Plasmo hot-reload ports—not your website’s localhost:3000.

For the Next security update, run this from the web app folder:

cd "C:\Users\lenovo\Documents\Resume Projects\Apply\apps\web
npm run dev