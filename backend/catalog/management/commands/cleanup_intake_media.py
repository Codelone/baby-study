from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.models import IntakePhoto


class Command(BaseCommand):
    help = "清理到期的私有图书照片，保留识别文本、字段来源和书目"

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=settings.INTAKE_MEDIA_RETENTION_DAYS)
        photos = IntakePhoto.objects.filter(created_at__lt=cutoff).exclude(file="")
        count = 0
        for photo in photos.iterator():
            photo.file.delete(save=True)
            count += 1
        self.stdout.write(f"cleaned={count}")
