#!/bin/sh
echo "Waiting for Kafka to be available at kafka:9092..."
while ! nc -z kafka 9092; do
  echo "Kafka is not available yet. Waiting..."
  sleep 5
done
echo "Kafka port is open. Waiting an additional 15 seconds for full initialization..."
sleep 15
echo "Starting the news service..."
exec python news_service.py
