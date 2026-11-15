# Deploying NDApp on Jetstream2

How the hosted copy of NDApp runs:

```
browser ──HTTPS──> Caddy (ports 80/443, automatic Let's Encrypt) ──> server.py on 127.0.0.1:8000 (systemd)
```

Files in `deploy/`:

| File | Purpose |
| --- | --- |
| `setup_vm.sh` | One script that installs everything on Ubuntu 24.04. Safe to rerun. |
| `ndapp.service` | systemd unit: starts the app on boot and restarts it if it crashes. |
| `Caddyfile.template` | Web server config: HTTPS and forwarding to the app. |

Settings live in `/etc/ndapp.env` on the machine (`HOST`, `PORT`, `NDAPP_USER`, `NDAPP_PASSWORD`).
The script generates a password the first time. Remove the two `NDAPP_` lines and run
`sudo systemctl restart ndapp` to make the site public.

## 1. Test it in WSL first (before the allocation is approved)

WSL Ubuntu needs systemd. Recent installs have it on already. Check with `systemctl status`. If that
fails, add this to `/etc/wsl.conf`, then run `wsl --shutdown` in PowerShell:

```
[boot]
systemd=true
```

Then, in the Ubuntu terminal:

```bash
sudo mkdir -p /opt/ndapp && sudo chown "$USER" /opt/ndapp
git clone https://github.com/yesh-krish/natural-disaster-risk-insurance-app /opt/ndapp
cd /opt/ndapp
sudo SKIP_DATA=1 bash deploy/setup_vm.sh
```

Open <http://localhost/> in Windows and log in with user `ndapp` and the password from
`sudo cat /etc/ndapp.env`. `SKIP_DATA=1` skips the multi-GB download. Remove it for a full test. You can
also copy your existing data in: `sudo cp -r "/mnt/c/Users/<you>/OneDrive/Documents/ChatGPT/NDApp/data/." /opt/ndapp/data/`
then `sudo chown -R ndapp:ndapp /opt/ndapp/data`.

The clone only contains **committed and pushed** code. Commit and push your work before testing. If the repo
is private, `git clone` asks for your GitHub username and a personal access token (not your password).

## 2. Create the Jetstream2 VM (after approval)

In Exosphere (jetstream2.exosphere.app), pick the allocation and create an instance:

- **Image:** Ubuntu 24.04
- **Flavor:** `m3.small` to start. Move up to `m3.quad` if map clicks feel slow.
- **Root disk:** 40 GB or more. The flood and fire rasters are about 2.2 GB, and the downloads need room to unpack.
- **Public IP:** on.

Exosphere shows the instance's hostname (something like
`<instance>.<project>.projects.jetstream-cloud.org`). Use it as the HTTPS domain below. Ports 80
and 443 must be open in the instance's security group. Caddy needs port 80 to get the certificate.

## 3. Deploy

Open the Exosphere web shell (or `ssh exouser@<hostname>`), then:

```bash
sudo mkdir -p /opt/ndapp && sudo chown "$USER" /opt/ndapp
git clone https://github.com/yesh-krish/natural-disaster-risk-insurance-app /opt/ndapp
cd /opt/ndapp
sudo bash deploy/setup_vm.sh <hostname-from-exosphere>
```

The script downloads the San Antonio flood tile (`prepare_data.py --tile n29w99`) and the CONUS
wildfire rasters (`prepare_fire_data.py --region CONUS`) straight onto the VM. That's faster than uploading
from home Wi-Fi. When it finishes it prints the site URL and where the password is.

To use the data from your laptop instead, run `sudo SKIP_DATA=1 bash deploy/setup_vm.sh <hostname>`,
then from PowerShell on your PC:

```bash
scp -r data exouser@<hostname>:/tmp/ndapp-data
```

and on the VM: `sudo cp -r /tmp/ndapp-data/. /opt/ndapp/data/ && sudo chown -R ndapp:ndapp /opt/ndapp/data`.

## Everyday commands (on the VM)

| Task | Command |
| --- | --- |
| Live logs | `journalctl -u ndapp -f` |
| Restart the app | `sudo systemctl restart ndapp` |
| Deploy new code | `cd /opt/ndapp && sudo git pull && sudo bash deploy/setup_vm.sh <hostname>` |
| Show the password | `sudo cat /etc/ndapp.env` |
| Caddy / HTTPS problems | `journalctl -u caddy -n 50` |

`setup_vm.sh` changes the checkout's owner to the `ndapp` service user, which is why `git pull` needs `sudo`.
If git says "dubious ownership", run `sudo git config --global --add safe.directory /opt/ndapp` once.

## Saving allocation credits

A running VM uses SUs every hour, even when nobody is visiting. **Shelve** it in Exosphere when the site
doesn't need to be up, and unshelve it later. The disk, data and settings are kept. The site comes back on
its own when the VM boots, because both services are enabled.
