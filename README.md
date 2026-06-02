<p align="center"><img src="docs/demo.gif" /></p>

# Kitchen Search

A desktop app for searching [Google's Emoji Kitchen](https://emojipedia.org/emoji-kitchen) combo images. I use this app all the time when messaging friends from my laptop.

Emojikitchen has approximately 147,000 images. Many of them are *delightful*. 

I found the existing Emoji Kitchen options missing essential features like a proper search feature, especially when combining specific emojis. It's convenient to set a keyboard shortcut to launch it like `Alt+Shift+K`, search or browse for fun emojis, and copy them into your messaging app of choice.

## Usage

There are a few options on the main menu. Once you find an image, you can click on it or hit enter to copy it to the clipboard.

* Normal search: just type in the search box, and the search function will find the emojikitchen images closest to that search. Some fun examples: "ski chicken", "those were fun times", or even just "volcano".
* Combo search: this is for quickly finding two base emojis to combine. For example, "ballet" and "cow" gives you a dancing cow.
* Story: type or paste in a story to make an image illustrating that story. For example, "I love to see stories about my favorite emojis :)".
* Settings: customize your experience by choosing whether to have copy notifications, exit the app on copy, keybinding for the app (Windows only), etc.

## Easy install (tested on Ubuntu and Windows)
## Linux

Check your system is compatible (should print `x86_64`, then a non-empty `$DISPLAY` like `:0` (means X11 or XWayland is reachable), then glibc 2.15 or newer):
```bash
uname -m
echo "${DISPLAY:-NO DISPLAY}"
ldd --version | head -1
```

Manual install:
* Go to the [releases](https://github.com/morganrivers/kitchensearch/releases) page 
* Download `kitchensearch-linux-x86_64.tar.gz`
* Extract the ZIP file
* Run `kitchensearch`

Or, on the terminal (zsh/bash/fish) just run:
```bash
wget https://github.com/morganrivers/kitchensearch/releases/download/v1.0.0/kitchensearch-linux-x86_64.tar.gz
tar -xzf kitchensearch-linux-x86_64.tar.gz
cd kitchensearch
./kitchensearch # launch the app
```
## Windows (10 or later)

* Go to the [releases](https://github.com/morganrivers/kitchensearch/releases) page
* Download `kitchensearch-windows-x86_64.exe`
* Run it and follow the installer

## Installing from source (tested on Ubuntu)

- Linux with X11 or Wayland (not sure which? run `echo $XDG_SESSION_TYPE`)
- **Python 3.8+**
- Tkinter (e.g. `sudo apt install python3-tk`)
```bash
git clone https://github.com/morganrivers/kitchensearch.git
sudo apt install python3-tk # install tkinter (python UI)
./scripts/install_from_source.sh # installs a venv and unzips app assets
.venv/bin/python3 kitchensearch.py # launch the app
```


### Disk usage

The base install uses ~400 MB (scripts + embedding data + Python packages).

Thumbnail images are cached as you browse (~10 KB each) in `~/.cache/kitchensearch/thumbs/`. The cache grows gradually with use but is automatically pruned to stay under 200 MB. The cache grows gradually with use but is automatically pruned to stay under 200 MB. Copied emojis can always be rediscovered offline as well.

## Uninstall

**Linux:** navigate to the folder where you unzipped kitchensearch and run:
```bash
rm -rf kitchensearch
rm -rf ~/.cache/kitchensearch
rm -rf ~/.config/kitchensearch
```

**Windows:** uninstall via **Settings → Apps** (or **Control Panel → Programs**) as with any program.

## Setting a Keyboard Shortcut

Launching the picker with a hotkey makes it instant to use from any app.

<details>
<summary><b>Windows</b></summary>

The installer sets `Alt+Shift+K` as the global hotkey automatically. A background daemon listens for it and opens the picker.

To change the hotkey: open the **Settings menu within the app** (or right-click the system tray icon → Settings), pick a new key combo, and save.

</details>

<details>
<summary><b>Linux — i3 / Sway</b></summary>

Add to your config (`~/.config/i3/config` or `~/.config/sway/config`), replacing the path with wherever you extracted the release:

```
bindsym alt+shift+k exec --no-startup-id ~/kitchensearch/kitchensearch
```

</details>

<details>
<summary><b>Linux — GNOME</b></summary>

1. Open **Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts**
2. Click **+**, set the name to `Kitchen Search`, and the command to the full path of the extracted binary (e.g. `/home/yourname/kitchensearch/kitchensearch`)
3. Assign your preferred key combo (e.g. `Alt+Shift+K`)

</details>

<details>
<summary><b>Linux — KDE Plasma</b></summary>

1. Open **System Settings → Shortcuts → Custom Shortcuts**
2. Click **Edit → New → Global Shortcut → Command/URL**
3. Set the command to the full path of the extracted binary (e.g. `/home/yourname/kitchensearch/kitchensearch`) and assign `Alt+Shift+K` as the trigger

See **[docs/README.md](docs/README.md)** for clipboard setup and the full tool reference.
