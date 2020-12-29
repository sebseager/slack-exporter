import os
import requests
from flask import Flask, request, Response
from urllib.parse import urljoin
from uuid import uuid4
import json
from dotenv import load_dotenv
from exporter import parse_replies, parse_channel_history

app = Flask(__name__)
load_dotenv(os.path.join(app.root_path, '.env'))


# chat write interactions

def post_response(response_url, text):
    requests.post(response_url, json={'text': text})


# pagination handling

def get_at_cursor(url, params, response_url, cursor=None):
    if cursor is not None:
        params['cursor'] = cursor

    r = requests.get(url, params=params)
    data = r.json()

    try:
        if data['ok'] is False:
            post_response(response_url, "I encountered an error: %s" % data['error'])

        next_cursor = None
        if 'response_metadata' in data and 'next_cursor' in data['response_metadata']:
            next_cursor = data['response_metadata']['next_cursor']
            if str(next_cursor).strip() == '':
                next_cursor = None

        return next_cursor, data

    except KeyError as e:
        post_response(response_url, "Something went wrong: %s." % e)
        return None, []


def paginated_get(url, params, response_url, combine_key=None):
    next_cursor = None
    result = []
    while True:
        next_cursor, data = get_at_cursor(url, params, response_url, cursor=next_cursor)
        try:
            result.extend(data) if combine_key is None else result.extend(data[combine_key])
        except KeyError:
            post_response(response_url, "Sorry! I got an unexpected response (KeyError).")
        if next_cursor is None:
            break

    return result


# GET requests

def channel_history(channel_id, response_url):
    params = {
        'token': os.environ['SLACK_USER_TOKEN'],
        'channel': channel_id,
        'limit': 200
    }

    return paginated_get('https://slack.com/api/conversations.history', params, response_url, combine_key='messages')


def user_list(team_id, response_url):
    params = {
        'token': os.environ['SLACK_USER_TOKEN'],
        'limit': 200,
        'team_id': team_id
    }

    return paginated_get('https://slack.com/api/users.list', params, response_url, combine_key='members')


def channel_replies(timestamps, channel_id, response_url):
    replies = []
    for timestamp in timestamps:
        params = {
            'token': os.environ['SLACK_USER_TOKEN'],
            'channel': channel_id,
            'ts': timestamp,
            'limit': 200
        }
        r = paginated_get('https://slack.com/api/conversations.replies', params, response_url, combine_key='messages')
        replies.append(r)

    return replies


# Flask routes

@app.route('/slack/export-channel', methods=['POST'])
def export_channel():
    data = request.form

    try:
        team_id = data['team_id']
        team_domain = data['team_domain']
        ch_id = data['channel_id']
        ch_name = data['channel_name']
        response_url = data['response_url']
        command_args = data['text']
    except KeyError:
        return Response("Sorry! I got an unexpected response (KeyError)."), 200

    post_response(response_url, "Retrieving history for this channel...")
    ch_hist = channel_history(ch_id, response_url)

    export_mode = str(command_args).lower()

    exports_subdir = 'exports'
    exports_dir = os.path.join(app.root_path, exports_subdir)
    file_ext = '.txt' if export_mode == 'text' else '.json'
    filename = "%s-ch_%s-%s%s" % (team_domain, ch_id, str(uuid4().hex)[:6], file_ext)
    filepath = os.path.join(exports_dir, filename)
    loc = urljoin(request.url_root, 'download/%s' % filename)

    if not os.path.isdir(exports_dir):
        os.makedirs(exports_dir, exist_ok=True)

    with open(filepath, mode='w') as f:
        if export_mode == 'text':
            num_msgs = len(ch_hist)
            sep = '=' * 24
            header_str = 'Channel Name: %s\nChannel ID: %s\n%s Messages\n%s\n\n' % (ch_name, ch_id, num_msgs, sep)
            data_ch = header_str + parse_channel_history(ch_hist, user_list(team_id, response_url))
            f.write(data_ch)
        else:
            json.dump(ch_hist, f, indent=4)

    post_response(response_url, "Done! This channel's history is available for download here (note that this link "
                                "is single-use): %s" % loc)

    return Response(), 200


@app.route('/slack/export-replies', methods=['POST'])
def export_replies():
    data = request.form

    try:
        team_id = data['team_id']
        team_domain = data['team_domain']
        ch_id = data['channel_id']
        ch_name = data['channel_name']
        response_url = data['response_url']
        command_args = data['text']
    except KeyError:
        return Response("Sorry! I got an unexpected response (KeyError)."), 200

    post_response(response_url, "Retrieving reply threads for this channel...")
    print(ch_id)
    ch_hist = channel_history(ch_id, response_url)
    print(ch_hist)
    ch_replies = channel_replies([x['ts'] for x in ch_hist if 'reply_count' in x], ch_id, response_url)

    export_mode = str(command_args).lower()

    exports_subdir = 'exports'
    exports_dir = os.path.join(app.root_path, exports_subdir)
    file_ext = '.txt' if export_mode == 'text' else '.json'
    filename = "%s-re_%s-%s%s" % (team_domain, ch_id, str(uuid4().hex)[:6], file_ext)
    filepath = os.path.join(exports_dir, filename)
    loc = urljoin(request.url_root, 'download/%s' % filename)

    if export_mode == 'text':
        header_str = 'Threads in: %s\n%s Messages' % (ch_name, len(ch_replies))
        data_replies = parse_replies(ch_replies, user_list(team_id, response_url))
        sep = '=' * 24
        data_replies = '%s\n%s\n\n%s' % (header_str, sep, data_replies)
    else:
        data_replies = ch_replies

    if not os.path.isdir(exports_dir):
        os.makedirs(exports_dir, exist_ok=True)

    with open(filepath, mode='w') as f:
        if export_mode == 'text':
            f.write(data_replies)
        else:
            json.dump(data_replies, f, indent=4)

    post_response(response_url, "Done! This channel's reply threads are available for download here (note that this "
                                "link is single-use): %s" % loc)

    return Response(), 200


@app.route('/download/<filename>')
def download(filename):
    path = os.path.join(app.root_path, 'exports', filename)

    def generate():
        with open(path) as f:
            yield from f
        os.remove(path)

    mimetype = 'text/plain' if os.path.splitext(filename)[-1] == '.txt' else 'application/json'

    r = app.response_class(generate(), mimetype=mimetype)
    r.headers.set('Content-Disposition', 'attachment', filename=filename)
    return r


if __name__ == '__main__':
    app.run(debug=False)
