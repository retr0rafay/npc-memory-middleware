FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY npc_middleware/ npc_middleware/
COPY static/ static/

EXPOSE 8000

CMD ["uvicorn", "npc_middleware.main:app", "--host", "0.0.0.0", "--port", "8000"]
