from __future__ import annotations

import requests


def main():
    response = requests.get(
        "https://www.google.com/search?q=cats"
        "&rlz=1C1ONGR_enGB1120GB1120"
        "&oq=cats"
        "&gs_lcrp=EgZjaHJvbWUqBggAEEUYOzIGCAAQRRg7MgcIARAAGI8CMgcIAhAAGI8C0gEIMTM3OGowajeoAgCwAgE"
        "&sourceid=chrome"
        "&ie=UTF-8"
        "&sei=RaznafqnBOqzhbIP7J6uoQE",
        timeout=10,
    )
    response.raise_for_status()

    print(response.status_code)
    print(response.text[:500])


if __name__ == "__main__":
    main()
