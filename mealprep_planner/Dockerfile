FROM python:3.13-alpine
RUN apk add --no-cache tzdata
WORKDIR /app
COPY mealprep ./mealprep
COPY static ./static
CMD ["python3", "-m", "mealprep"]
