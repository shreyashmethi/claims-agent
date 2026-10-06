FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements-llm.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-llm.txt
COPY . .
RUN python cli.py seed
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
