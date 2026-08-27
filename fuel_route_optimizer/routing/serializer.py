from rest_framework import serializers


class RouteOptimizeSerializer(serializers.Serializer):
    start_location = serializers.CharField(max_length=150)
    finish_location = serializers.CharField(max_length=150)

    def validate_start_location(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError("Start location cannot be empty.")

        return value

    def validate_finish_location(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError("Finish location cannot be empty.")

        return value
