"""Check the nginx site template with an installed binary, without starting nginx."""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


SITE_CONFIG = Path(__file__).resolve().parents[1] / "nginx" / "polypbase.conf"


class NginxConfigTests(unittest.TestCase):
    def test_site_config_syntax(self):
        nginx = shutil.which("nginx") or shutil.which("/usr/sbin/nginx")
        if nginx is None:
            self.skipTest("nginx is not installed on PATH or at /usr/sbin/nginx")

        site = SITE_CONFIG.read_text(encoding="utf-8")
        # Some nginx builds bind listeners during -t. Keep them on loopback.
        listeners = {"80": "127.0.0.1:18080", "[::]:80": "[::1]:18080"}
        site, count = re.subn(
            r"(?m)^([ \t]*listen[ \t]+)([^;\n]+);",
            lambda match: match[1] + listeners[match[2]] + ";",
            site,
        )
        self.assertEqual(count, len(listeners))

        with tempfile.TemporaryDirectory(prefix="polypbase-nginx-") as directory:
            prefix = Path(directory)
            (prefix / "polypbase.conf").write_text(site, encoding="utf-8")
            config = prefix / "nginx.conf"
            config.write_text(
                "error_log stderr;\n"
                "pid nginx.pid;\n"
                "events {}\n"
                "http {\n"
                "    access_log off;\n"
                "    client_body_temp_path client_body;\n"
                "    proxy_temp_path proxy;\n"
                "    fastcgi_temp_path fastcgi;\n"
                "    uwsgi_temp_path uwsgi;\n"
                "    scgi_temp_path scgi;\n"
                "    include polypbase.conf;\n"
                "}\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [nginx, "-t", "-p", str(prefix), "-c", str(config), "-e", "stderr"],
                cwd=prefix,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
