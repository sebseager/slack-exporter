#!/usr/bin/env python3
import os
import sys
import requests
import json
from datetime import datetime
import argparse
from dotenv import load_dotenv

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


# pagination handling


def get_at_cursor(url, params, cursor=None, response_url=None):
    if cursor is not None:
        params["cursor"] = cursor

    # slack api (OAuth 2.0) now requires auth tokens in HTTP Authorization header
    # instead of passing it as a query parameter
    try:
        headers = {"Authorization": "Bearer %s" % os.environ["SLACK_USER_TOKEN"]}
    except KeyError:
        handle_print("Missing SLACK_USER_TOKEN in environment variables", response_url)
        sys.exit(1)

    r = requests.get(url, headers=headers, params=params)

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
        except KeyError:
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


def channel_history(channel_id, response_url=None, oldest=None, latest=None):
    params = {
        # "token": os.environ["SLACK_USER_TOKEN"],
        "channel": channel_id,
        "limit": 200,
        "oldest": oldest,
        "latest": latest,
    }

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
            ch_ownership = "created by %s" % name_from_uid(channel["creator"], users)
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
        if user["id"] == user_id:
            return user["real_name"] if real else user["name"]
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
        if "name" in u:
            entry += " %s" % u["name"]
        if "real_name" in u:
            entry += " (%s)" % u["real_name"]
        if "tz" in u:
            entry += ", %s" % u["tz"]

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
        u_type = u_type[:-1] if u_type.endswith("|") else u_type
        entry += ", " if u_type.strip() != "" else ""
        entry += "%s\n" % u_type
        result += entry

    return result


def parse_channel_history(msgs, users, check_thread=False):
    if "messages" in msgs:
        msgs = msgs["messages"]

    messages = [x for x in msgs if x["type"] == "message"]  # files are also messages
    body = ""
    for msg in messages:
        if "user" in msg:
            usr = {
                "name": name_from_uid(msg["user"], users),
                "real_name": name_from_uid(msg["user"], users, True),
            }
        else:
            usr = {"name": "", "real_name": "none"}

        timestamp = datetime.fromtimestamp(round(float(msg["ts"]))).strftime(
            "%m-%d-%y %H:%M:%S"
        )
        text = msg["text"] if msg["text"].strip() != "" else "[no message content]"
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
                " - [%s] %s, %s" % (f["id"], f["name"], f["url_private_download"])
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
        "--ch", help="Restrict to given Channel ID"
    )
    parser.add_argument(
        "--fr", help="Unix timestamp for earliest message"
    )
    parser.add_argument(
        "--to", help="Unix timestamp for latest message"
    )
    parser.add_argument(
        "-r",
        action="store_true",
        help="Get reply threads for all accessible conversations",
    )
    a = parser.parse_args()

    ts = str(datetime.strftime(datetime.now(), "%m-%d-%Y_%H%M%S"))

    def save(data, filename):
        if a.o is None:
            print(data)
        else:
            out_dir_parent = os.path.abspath(
                os.path.expanduser(os.path.expandvars(a.o))
            )
            out_dir = os.path.join(out_dir_parent, "slack_export_%s" % ts)
            filename = filename + ".json" if a.json else filename + ".txt"
            os.makedirs(out_dir, exist_ok=True)
            full_filepath = os.path.join(out_dir, filename)
            print("Writing output to %s" % full_filepath)
            with open(full_filepath, mode="w") as f:
                if a.json:
                    json.dump(data, f, indent=4)
                else:
                    f.write(data)

    def save_replies(channel_hist, channel_id, users):
        ch_replies = channel_replies(
            [x["ts"] for x in channel_hist if "reply_count" in x], channel_id
        )
        if a.json:
            data_replies = ch_replies
        else:
            ch_name, ch_type = name_from_ch_id(ch_id, ch_list)
            header_str = "Threads in %s: %s\n%s Messages" % (
                ch_type,
                ch_name,
                len(ch_replies),
            )
            data_replies = parse_replies(ch_replies, users)
            sep = "=" * 24
            data_replies = "%s\n%s\n\n%s" % (header_str, sep, data_replies)
        save(data_replies, "channel-replies_%s" % channel_id)

    def save_channel(ch_id, ch_list, users):
        ts_fr = a.fr
        ts_to = a.to
        ch_hist = channel_history(ch_id, oldest=ts_fr, latest=ts_to)
        if a.json:
            data_ch = ch_hist
        else:
            data_ch = parse_channel_history(ch_hist, users)
            ch_name, ch_type = name_from_ch_id(ch_id, ch_list)
            header_str = "%s Name: %s" % (ch_type, ch_name)
            sep = "=" * 24
            data_ch = (
                "Channel ID: %s\n%s\n%s Messages\n%s\n\n"
                % (ch_id, header_str, len(ch_hist), sep)
                + data_ch
            )
        save(data_ch, "channel_%s" % ch_id)
        if a.r:
            save_replies(ch_hist, ch_id, users)

    if a.lc:
        data = (
            channel_list()
            if a.json
            else parse_channel_list(channel_list(), user_list())
        )
        save(data, "channel_list")
    if a.lu:
        data = user_list() if a.json else parse_user_list(user_list())
        save(data, "user_list")
    if a.c:
        ch_id = a.ch if a.ch else (os.environ["CHANNEL_ID"] if "CHANNEL_ID" in os.environ else None)
        ch_list = channel_list()
        users = user_list()
        if ch_id:
            save_channel(ch_id, ch_list, users)
        else:
            for ch_id in [x["id"] for x in ch_list]:
                save_channel(ch_id, ch_list, users)
    # elif, since we want to avoid asking for channel_history twice
    elif a.r:
        for ch_id in [x["id"] for x in channel_list()]:
            save_replies(channel_history(ch_id), ch_id, user_list())
