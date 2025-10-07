# Nerdwave Radio Controller

This is the git project for the Nerdwave website, https://nerdwave.cc.

Nerdwave is a system to control an external player such as MPD,
or a streaming source such as Ices or LiquidSoap. It cannot play
or stream audio by itself.

The software stack and data flow for broadcasting:

-   LiquidSoap asks Nerdwave backend what song/MP3 file should be played
-   Nerdwave backend replies with a song/MP3 file
-   LiquidSoap plays the song, encodes the stream, and sends the audio to Icecast
-   Icecast distributes audio to users
-   Icecast tells Nerdwave API when users tune in/out

Nerdwave only supports reading tags from MP3 files.

## Prerequisites

Authentication for Nerdwave users is dependant on Discord.

-   Enable external auth by placing your app keys in the config file
-   If you're just running Nerdwave for streaming audio, you do not need Discord.
-   If you are just testing/developing locally, you do not need Discord.

If using Icecast, Icecast 2.3.3 or above is required.

If using LiquidSoap, LiquidSoap 1.1 or above is required.

### Prerequisites on Debian/Ubuntu

Nerdwave is designed to run on Python 3.7 using `pipenv`. It also requires installation
of various media libraries to run volume analysis on its files.

```
git clone https://github.com/rmcauley/nerdwave.git
cd nerdwave
sudo apt-get install pipenv libpq-dev python3.7-dev libmemcached-dev
sudo apt-get install gir1.2-gstreamer-1.0 \
     gstreamer1.0-plugins-base \
     gstreamer1.0-plugins-good \
     libcairo2-dev \
     libgirepository1.0-dev
cp nerdwave/etc/nerdwave_reference.conf nerdwave/etc/$USER.conf
pipenv install
```

## Postgres Setup

```
sudo -u postgres createdb nerdwave
sudo -u postgres psql -d nerdwave -c "CREATE EXTENSION IF NOT EXISTS pg_trgm"
```

## Configure Nerdwave

Edit your configuration file in the Nerdwave `etc/`, and follow the instructions
within to setup your install. Please read through the entire config carefully.
Some options are very important.

Tips:

-   Until you're ready to deploy a production version, it's best to leave development mode
    on and keep Nerdwave single-processed.
-   Do not create a station with ID 0 - ID 0 is reserved.

Potential gotcha:

If you start seeing "Peer authentication failed" messages when running Nerdwave,
you _may_ have to edit your pg_hba.conf after this if you get errors trying to
connect. The pg_hba.conf is usually located at `/etc/postgresql/[VERSION]/main/pg_hba.conf`.
If you're running this all on the same machine, add this line to the file:

```
local    [DATABASE NAME]     [DATABASE USER]         md5
```

## Adding Music to your Nerdwave Library

Locate the "song_dir" entry from your configuration file and copy/paste
your music library to this directory.

-   _Your MP3 tags must be accurate_. Nerdwave reads the tags to obtain
    track information, which is necessary to manage song rotation.
-   Upload a minimum of 1,000 songs. Nerdwave requires a minimal library
    of this size to operate correctly.
-   Place albums in separate directories if using album art. To add album art,
    create a file named "folder.jpg" and place it in each album directory
    for it to appear. (sorry, embedded album art is not supported)
-   Nerdwave and LiquidSoap support unicode MP3 tags, but do not support
    unicode filenames. Please rename files that contain accents and symbols.
    Nerdwave will reject and skip files that contain accents or symbols.

## First Start and Test

Open a terminal/command line to your Nerdwave repository directory.
You need to initialize the database and then add your songs to the database:

```
python db_init.py
```

Now have Nerdwave pick up the music you added to your `song_dirs`:

```
python nw_scanner.py --full
```

This will run a full scan of your `song_dirs`. To monitor `song_dirs`
continuously without using CPU, run `nw_scanner.py` without the `--full` switch.

Once done, open another terminal/command line and start the music
management backend that LiquidSoap talks to:

```
python nw_backend.py
```

Once that is started successfully, open another terminal/command line
and start the public-facing website and API:

```
python nw_api.py
```

Now use the same tool LiquidSoap uses to test that everything works:

```
python nw_get_next.py
```

If successful, you should see a song file name as output.

Open the beta URL at `/beta/?sid=1` to see your Nerdwave.

If you are not running against an installed phpBB and want
to emulate being logged in, open `/api4/test/login_tuned_in/1`.

## Deploying to Production

### Installing a Production Nerdwave

Before running `install.py`:

Copy your Nerdwave configuration file to `/etc/nerdwave.conf`
and tune for production. e.g. Turn off development modes,
turn down logging, increase number of processes to the
number of CPUs you have.

Init scripts are included for systemd but are not
installed for you by default.

`install.py` will attempt to automatically run
`/etc/init.d/nerdwave` with `start` and `stop` arguments if
the script exists.

`sudo python install.py` when ready, and Nerdwave will be copied to
`/opt/nerdwave`, and will minify and bundle Javascript to be put
into the "baked" static directory.

Nerdwave depends on three daemons:

-   `nw_backend.py` to act for LiquidSoap
-   `nw_api.py` to act for browsers
-   `nw_scanner.py` which monitors the filesystem for new/changed songs

### Updating a Running Nerdwave

During installation, Nerdwave has no safety mechanisms or rolling restarts.
Nerdwave will appear to be shutdown for a few seconds to the outside
world while installing/updating. If the restart fails, Nerdwave will be
offline. ([GitHub Issue](https://github.com/rmcauley/nerdwave/issues/95))

### Icecast and Listener Tune In/Out Recognition

For user tune in recognition to work, you have to use Icecast's
user authentication system. Sample Icecast configurations are
included in Nerdwave's `etc/` directory.

### LiquidSoap Configuration

A sample LiquidSoap configuration is included in `etc/`.

## Developing The Front-End

To edit the site:

-   Using Visual Studio Code is recommended
-   Install the Python and Prettier extensions for VSCode.
-   Set "RW_ENVIRONMENT" environment variable to "develop"

File locations:

-   HTML files for the index and admin panels can be found in `templates/`.
-   HTML templates for the main site can be found in `static/templates5/`.
-   CSS files are in `static/style5` and the entry point is `r5.scss`.
-   Image files are in `static/images4`.
-   JS files are in `static/js5`:
    -   Execution starts from `main.js`.
    -   No libraries or frameworks are used

Nerdwave rebuilds CSS on each page load of `/beta` when
development mode is on, and `/beta` serves the Javascript without
minification or bundling. No need for npm, package managers,
or watchers.

## Contact

You can get help for deployment and development through the main
developer, Rob, on the Nerdwave discord:

-   https://discord.gg/fdb2cs7puS
