import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "创建家庭共用试用账户；凭据通过环境变量传入，不覆盖已有账户"

    def handle(self, *args, **options):
        username = os.environ.get("FAMILY_TRIAL_USERNAME", "")
        password = os.environ.get("FAMILY_TRIAL_PASSWORD", "")
        if not username or len(password) < 20:
            raise CommandError("请通过家庭启动脚本生成试用账户凭据")
        with transaction.atomic():
            user = get_user_model().objects.filter(username=username).first()
            if user is None:
                get_user_model().objects.create_user(
                    username=username,
                    password=password,
                    is_active=True,
                    is_staff=True,
                    is_superuser=False,
                )
                self.stdout.write(f"已创建家庭试用账户：{username}")
            elif (
                not user.check_password(password)
                or not user.is_active
                or not user.is_staff
                or user.is_superuser
                or user.groups.exists()
                or user.user_permissions.exists()
            ):
                raise CommandError("同名账户与试用配置不一致；拒绝覆盖密码或权限")
            else:
                self.stdout.write(f"复用家庭试用账户：{username}")
