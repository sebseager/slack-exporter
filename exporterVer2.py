#!/usr/bin/env python3
import os
import sys
import requests
import json
from timeit import default_timer
from datetime import datetime
import argparse
from dotenv import load_dotenv
from pathvalidate import sanitize_filename
from time import sleep

# when rate-limited, add this to the wait time
ADDITIONAL_SLEEP_TIME = 2

env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.isfile(env_file):
    load_dotenv(env_file)


# write handling


def post_response(response_url, text):
    requests.post(response_url, json={"text": text})


# use this to say anything
# will print to stdout if no response_url is given
# or post_response to given url if provided
def handle_print(text, response_url=None):
    if response_url is None:
        print(text)
    else:
        post_response(response_url, text)


# slack api (OAuth 2.0) now requires auth tokens in HTTP Authorization header
# instead of passing it as a query parameter
try:
    HEADERS = {"Authorization": "Bearer %s" % os.environ["SLACK_USER_TOKEN"]}
except KeyError:
    handle_print(
        "Missing SLACK_USER_TOKEN in environment variables", response_url)
    sys.exit(1)


def _get_data(url, params):
    return requests.get(url, headers=HEADERS, params=params)


def get_data(url, params):
    """Naively deals with rate-limiting"""

    # success means "not rate-limited", it can still end up with error
    success = False
    attempt = 0

    while not success:
        r = _get_data(url, params)
        attempt += 1

        if r.status_code != 429:
            success = True
        else:
            retry_after = int(r.headers["Retry-After"])  # seconds to wait
            sleep_time = retry_after + ADDITIONAL_SLEEP_TIME
            print(
                f"Rate-limited. Retrying after {sleep_time} seconds ({attempt}x).")
            sleep(sleep_time)
    return r


# pagination handling


def get_at_cursor(url, params, cursor=None, response_url=None):
    if cursor is not None:
        params["cursor"] = cursor

    r = get_data(url, params)

    if r.status_code != 200:
        handle_print("ERROR: %s %s" % (r.status_code, r.reason), response_url)
        sys.exit(1)

    d = r.json()

    try:
        if d["ok"] is False:
            handle_print("I encountered an error: %s" % d, response_url)
            sys.exit(1)

        next_cursor = None
        if "response_metadata" in d and "next_cursor" in d["response_metadata"]:
            next_cursor = d["response_metadata"]["next_cursor"]
            if str(next_cursor).strip() == "":
                next_cursor = None

        return next_cursor, d

    except KeyError as e:
        handle_print("Something went wrong: %s." % e, response_url)
        return None, []


def paginated_get(url, params, combine_key=None, response_url=None):
    next_cursor = None
    result = []
    while True:
        next_cursor, data = get_at_cursor(
            url, params, cursor=next_cursor, response_url=response_url
        )

        try:
            result.extend(data) if combine_key is None else result.extend(
                data[combine_key]
            )
        except KeyError as e:
            handle_print("Something went wrong: %s." % e, response_url)
            sys.exit(1)

        if next_cursor is None:
            break

    return result


# GET requests


def channel_list(team_id=None, response_url=None):
    params = {
        # "token": os.environ["SLACK_USER_TOKEN"],
        "team_id": team_id,
        "types": "public_channel,private_channel,mpim,im",
        "limit": 200,
    }

    return paginated_get(
        "https://slack.com/api/conversations.list",
        params,
        combine_key="channels",
        response_url=response_url,
    )


def get_file_list(channel_id=None):
    current_page = 1
    total_pages = 1
    while current_page <= total_pages:
        params = {"page": current_page}
        if channel_id:
            # Add the channel_id parameter if specified
            params["channel"] = channel_id
        response = get_data("https://slack.com/api/files.list", params=params)
        json_data = response.json()
        total_pages = json_data["paging"]["pages"]
        for file in json_data["files"]:
            yield file
        current_page += 1


def channel_history(channel_id, response_url=None, oldest=None, latest=None):
    params = {
        # "token": os.environ["SLACK_USER_TOKEN"],
        "channel": channel_id,
        "limit": 200,
    }

    if oldest is not None:
        params["oldest"] = oldest
    if latest is not None:
        params["latest"] = latest

    return paginated_get(
        "https://slack.com/api/conversations.history",
        params,
        combine_key="messages",
        response_url=response_url,
    )


def user_list(team_id=None, response_url=None):
    params = {
        # "token": os.environ["SLACK_USER_TOKEN"],
        "limit": 200,
        "team_id": team_id,
    }

    return paginated_get(
        "https://slack.com/api/users.list",
        params,
        combine_key="members",
        response_url=response_url,
    )


def channel_replies(timestamps, channel_id, response_url=None):
    replies = []
    for timestamp in timestamps:
        params = {
            # "token": os.environ["SLACK_USER_TOKEN"],
            "channel": channel_id,
            "ts": timestamp,
            "limit": 200,
        }
        replies.append(
            paginated_get(
                "https://slack.com/api/conversations.replies",
                params,
                combine_key="messages",
                response_url=response_url,
            )
        )

    return replies


# parsing


def parse_channel_list(channels, users):
    result = ""
    for channel in channels:
        ch_id = channel["id"]
        ch_name = channel["name"] if "name" in channel else ""
        ch_private = (
            "private " if "is_private" in channel and channel["is_private"] else ""
        )
        if "is_im" in channel and channel["is_im"]:
            ch_type = "direct_message"
        elif "is_mpim" in channel and channel["is_mpim"]:
            ch_type = "multiparty-direct_message"
        elif "group" in channel and channel["is_group"]:
            ch_type = "group"
        else:
            ch_type = "channel"
        if "creator" in channel:
            ch_ownership = "created by %s" % name_from_uid(
                channel["creator"], users)
        elif "user" in channel:
            ch_ownership = "with %s" % name_from_uid(channel["user"], users)
        else:
            ch_ownership = ""
        ch_name = " %s:" % ch_name if ch_name.strip() != "" else ch_name
        result += "[%s]%s %s%s %s\n" % (
            ch_id,
            ch_name,
            ch_private,
            ch_type,
            ch_ownership,
        )

    return result


def name_from_uid(user_id, users, real=False):
    for user in users:
        if user["id"] != user_id:
            continue

        if real:
            try:
                return user["profile"]["real_name"]
            except KeyError:
                try:
                    return user["profile"]["display_name"]
                except KeyError:
                    return "[no full name]"
        else:
            return user["name"]

    return "[null user]"


def name_from_ch_id(channel_id, channels):
    for channel in channels:
        if channel["id"] == channel_id:
            return (
                (channel["user"], "Direct Message")
                if "user" in channel
                else (channel["name"], "Channel")
            )
    return "[null channel]"


def parse_user_list(users):
    result = ""
    for u in users:
        entry = "[%s]" % u["id"]

        try:
            entry += " %s" % u["name"]
        except KeyError:
            pass

        try:
            entry += " (%s)" % u["profile"]["real_name"]
        except KeyError:
            pass

        try:
            entry += ", %s" % u["tz"]
        except KeyError:
            pass

        u_type = ""
        if "is_admin" in u and u["is_admin"]:
            u_type += "admin|"
        if "is_owner" in u and u["is_owner"]:
            u_type += "owner|"
        if "is_primary_owner" in u and u["is_primary_owner"]:
            u_type += "primary_owner|"
        if "is_restricted" in u and u["is_restricted"]:
            u_type += "restricted|"
        if "is_ultra_restricted" in u and u["is_ultra_restricted"]:
            u_type += "ultra_restricted|"
        if "is_bot" in u and u["is_bot"]:
            u_type += "bot|"
        if "is_app_user" in u and u["is_app_user"]:
            u_type += "app_user|"

        if u_type.endswith("|"):
            u_type = u_type[:-1]

        entry += ", " if u_type.strip() != "" else ""
        entry += "%s\n" % u_type
        result += entry

    return result


def parse_channel_history(msgs, users, check_thread=False):
    if "messages" in msgs:
        msgs = msgs["messages"]

    messages = [x for x in msgs if x["type"] ==
                "message"]  # files are also messages
    body = ""
    for msg in messages:
        if "user" in msg:
            usr = {
                "name": name_from_uid(msg["user"], users),
                "real_name": name_from_uid(msg["user"], users, real=True),
            }
        else:
            usr = {"name": "", "real_name": "none"}

        timestamp = datetime.fromtimestamp(round(float(msg["ts"]))).strftime(
            "%m-%d-%y %H:%M:%S"
        )
        text = msg["text"] if msg["text"].strip(
        ) != "" else "[no message content]"
        for u in [x["id"] for x in users]:
            text = str(text).replace(
                "<@%s>" % u, "<@%s> (%s)" % (u, name_from_uid(u, users))
            )

        entry = "Message at %s\nUser: %s (%s)\n%s" % (
            timestamp,
            usr["name"],
            usr["real_name"],
            text,
        )
        if "reactions" in msg:
            rxns = msg["reactions"]
            entry += "\nReactions: " + ", ".join(
                "%s (%s)"
                % (x["name"], ", ".join(name_from_uid(u, users) for u in x["users"]))
                for x in rxns
            )
        if "files" in msg:
            files = msg["files"]
            deleted = [
                f for f in files if "name" not in f or "url_private_download" not in f
            ]
            ok_files = [f for f in files if f not in deleted]
            entry += "\nFiles:\n"
            entry += "\n".join(
                " - [%s] %s, %s" % (f["id"], f["name"],
                                    f["url_private_download"])
                for f in ok_files
            )
            entry += "\n".join(
                " - [%s] [deleted, oversize, or unavailable file]" % f["id"]
                for f in deleted
            )

        entry += "\n\n%s\n\n" % ("*" * 24)

        if check_thread and "parent_user_id" in msg:
            entry = "\n".join("\t%s" % x for x in entry.split("\n"))

        body += entry.rstrip(
            "\t"
        )  # get rid of any extra tabs between trailing newlines

    return body


def parse_replies(threads, users):
    body = ""
    for thread in threads:
        body += parse_channel_history(thread, users, check_thread=True)
        body += "\n"

    return body


def download_file(destination_path, url, attempt=0):
    if os.path.exists(destination_path):
        print("Skipping existing %s" % destination_path)
        return True

    print(f"Downloading file on attempt {attempt} to {destination_path}")

    try:
        response = requests.get(url, headers=HEADERS)
        with open(destination_path, "wb") as fh:
            fh.write(response.content)
    except Exception as err:
        print(
            f"Unexpected error on {destination_path} attempt {attempt}; {err=}, {type(err)=}")
        return False
    else:
        return True


def save_files(file_dir, channel_id=None):
    total = 0
    start = default_timer()
    for file_info in get_file_list(channel_id=channel_id):
        url = file_info["url_private"]
        file_info["name"] = sanitize_filename(file_info["name"])
        destination_filename = "{id}-{name}".format(**file_info)
        os.makedirs(file_dir, exist_ok=True)
        destination_path = os.path.join(file_dir, destination_filename)

        download_success = False
        attempt = 1
        while not download_success and attempt <= 10:
            download_success = download_file(destination_path, url, attempt)
            attempt += 1

        if not download_success:
            raise Exception(
                "Failed to download from {url} after {attempt} tries")

        total += 1

    end = default_timer()
    seconds = int(end - start)
    print("Downloaded %i files in %i seconds" % (total, seconds))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-o",
        help="Directory in which to save output files (if left blank, prints to stdout)",
    )
    parser.add_argument(
        "--lc", action="store_true", help="List all conversations in your workspace"
    )
    parser.add_argument(
        "--lu", action="store_true", help="List all users in your workspace"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Give the requested output in raw JSON format (no parsing)",
    )
    parser.add_argument(
        "-c", action="store_true", help="Get history for all accessible conversations"
    )
    parser.add_argument(
        "--ch", help="With -c, restrict export to given channel ID")
    parser.add_argument(
        "--fr",
        help="With -c, Unix timestamp (seconds since Jan. 1, 1970) for earliest message",
        type=str,
    )
    parser.add_argument(
        "--to",
        help="With -c, Unix timestamp (seconds since Jan. 1, 1970) for latest message",
        type=str,
    )
    parser.add_argument(
        "-r",
        action="store_true",
        help="Get reply threads for all accessible conversations",
    )
    parser.add_argument(
        "--files",
        action="store_true",
        help="Download all files",
    )

    a = parser.parse_args()
    ts = str(datetime.strftime(datetime.now(), "%m-%d-%Y_%H%M%S"))
    sep_str = "*" * 24

    if a.o is None and a.files:
        print("If you specify --files you also need to specify an output directory with -o")
        sys.exit(1)

    if a.o is not None:
        out_dir_parent = os.path.abspath(
            os.path.expanduser(os.path.expandvars(a.o))
        )
        out_dir = os.path.join(out_dir_parent, "slack_export_%s" % ts)

    def save(data, filename):
        if a.o is None:
            json.dump(data, sys.stdout, indent=4)
        else:
            filename = filename + ".json" if a.json else filename + ".txt"
            os.makedirs(out_dir, exist_ok=True)
            full_filepath = os.path.join(out_dir, filename)
            print("Writing output to %s" % full_filepath)
            with open(full_filepath, mode="w", encoding="utf-8") as f:
                if a.json:
                    json.dump(data, f, indent=4)
                else:
                    f.write(data)

    def save_replies(channel_hist, channel_id, channel_list, users):
        reply_timestamps = [x["ts"]
                            for x in channel_hist if "reply_count" in x]
        ch_replies = channel_replies(reply_timestamps, channel_id)
        if a.json:
            data_replies = ch_replies
        else:
            ch_name, ch_type = name_from_ch_id(channel_id, channel_list)
            header_str = "Threads in %s: %s\n%s Messages" % (
                ch_type,
                ch_name,
                len(ch_replies),
            )
            data_replies = parse_replies(ch_replies, users)
            data_replies = "%s\n%s\n\n%s" % (header_str, sep_str, data_replies)
        save(data_replies, "channel-replies_%s" % channel_id)

    def save_channel(channel_hist, channel_id, channel_list, users):
        if a.json:
            data_ch = channel_hist
        else:
            data_ch = parse_channel_history(channel_hist, users)
            ch_name, ch_type = name_from_ch_id(channel_id, channel_list)
            header_str = "%s Name: %s" % (ch_type, ch_name)
            data_ch = (
                "Channel ID: %s\n%s\n%s Messages\n%s\n\n"
                % (channel_id, header_str, len(channel_hist), sep_str)
                + data_ch
            )
        save(data_ch, "channel_%s" % channel_id)
        if a.r:
            save_replies(channel_hist, channel_id, channel_list, users)

    ch_list = channel_list()
    user_list = user_list()

    if a.lc:
        data = ch_list if a.json else parse_channel_list(ch_list, user_list)
        save(data, "channel_list")
    if a.lu:
        data = user_list if a.json else parse_user_list(user_list)
        save(data, "user_list")
    if a.c:
        ch_id = a.ch
        if ch_id:
            ch_hist = channel_history(ch_id, oldest=a.fr, latest=a.to)
            save_channel(ch_hist, ch_id, ch_list, user_list)
        else:
            for ch_id in [x["id"] for x in ch_list]:
                ch_hist = channel_history(ch_id, oldest=a.fr, latest=a.to)
                save_channel(ch_hist, ch_id, ch_list, user_list)
    # elif, since we want to avoid asking for channel_history twice
    elif a.r:
        for ch_id in [x["id"] for x in channel_list()]:
            ch_hist = channel_history(ch_id, oldest=a.fr, latest=a.to)
            save_replies(ch_hist, ch_id, ch_list, user_list)

    if a.files and a.o is not None:
        if a.ch:
            ch_id = a.ch
            save_files(out_dir, channel_id=ch_id)
        else:
            save_files(out_dir)
