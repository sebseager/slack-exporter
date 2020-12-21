import os
import ssl
import slack
from slack.errors import SlackApiError
import requests
from dotenv import load_dotenv
from flask import Flask, request, Response
from urllib.parse import urljoin
from uuid import uuid4
import json
from datetime import datetime

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

app = Flask(__name__)
load_dotenv(os.path.join(app.root_path, '.env'))
client = slack.WebClient(token=os.environ['SLACK_BOT_TOKEN'], ssl=ssl_context)


# chat write interactions

def send_to_channel(channel_id, text):
    try:
        client.chat_postMessage(channel=channel_id, text=text)
    except SlackApiError as e:
        print(e)
        pass


def send_to_user(user_id, text):
    try:
        client.chat_postMessage(channel=user_id, text=text, as_user=True)
    except SlackApiError as e:
        print(e)
        pass


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
        result.extend(data) if combine_key is None else result.extend(data[combine_key])
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


# parsing

def user_list_to_names(user_dict):
    return {x['id']: {'name': x['name'], 'real_name': x['real_name']} for x in user_dict}


def channel_history_to_text(msgs_dict, users):
    messages = [x for x in msgs_dict['messages'] if x['type'] == 'message']  # files are also messages
    body = 'Team ID: %s\nTeam Domain: %s\nChannel ID: %s\nChannel Name: %s\n\n' % \
           (msgs_dict['team_id'], msgs_dict['team_domain'], msgs_dict['channel_id'], msgs_dict['channel_name'])
    body += '%s\n %s Messages\n%s\n\n' % ('=' * 16, len(messages), '=' * 16)
    for msg in messages:
        usr = users[msg['user']] if 'user' in msg else {'name': '', 'real_name': 'none'}
        ts = datetime.fromtimestamp(round(float(msg['ts']))).strftime('%m-%d-%Y %H:%M:%S')
        text = msg['text'] if msg['text'].strip() != "" else "[no message content]"
        for u in users.keys():
            # if u in text:
            #     print(u)
            text = str(text).replace('<@%s>' % u, '<@%s> (%s)' % (u, users[u]['name']))
        entry = "Message at %s\nUser: %s (%s)\n%s" % (ts, usr['name'], usr['real_name'], text)
        if 'reactions' in msg:
            rxns = msg['reactions']
            entry += "\nReactions: " + ', '.join('%s (%s)' % (x['name'], ', '.join(
                users[u]['name'] for u in x['users'])) for x in rxns)
        if 'files' in msg:
            files = msg['files']
            entry += "\nFiles:\n" + '\n'.join(' - %s, %s' % (f['name'], f['url_private_download']) for f in files)

        body += entry.strip() + '\n\n%s\n\n' % ('=' * 16)

    return body


# Flask routes

@app.route('/slack/export-channel', methods=['POST'])
def export_channel():
    data = request.form

    try:
        team_id = data['team_id']
        team_domain = data['team_domain']
        channel_id = data['channel_id']
        channel_name = data['channel_name']
        response_url = data['response_url']
        command_args = data['text']
    except KeyError:
        return Response("Sorry! I got an unexpected response from Slack (KeyError)."), 200

    post_response(response_url, "Retrieving history for this channel...")
    all_messages = {
        'team_id': team_id,
        'team_domain': team_domain,
        'channel_id': channel_id,
        'channel_name': channel_name,
        'messages': channel_history(channel_id, response_url)
    }

    filename = "%s-%s-%s.json" % (team_domain, channel_id, str(uuid4().hex)[:6])
    filepath = os.path.join(app.root_path, 'exports', filename)
    loc = urljoin(request.url_root, 'download/%s' % filename)

    with open(filepath, mode='w') as f:
        if str(command_args).lower() == 'text':
            users = user_list_to_names(user_list(team_id, response_url))
            f.write(channel_history_to_text(all_messages, users))
        else:
            json.dump(all_messages, f, indent=4)

    post_response(response_url, "Done! This channel's history is available for download here (note that this link "
                                "is single-use): %s" % loc)

    return Response(), 200


@app.route('/download/<filename>')
def download(filename, mimetype='application/json'):
    path = os.path.join(app.root_path, 'exports', filename)

    def generate():
        with open(path) as f:
            yield from f
        os.remove(path)

    r = app.response_class(generate(), mimetype=mimetype)
    r.headers.set('Content-Disposition', 'attachment', filename=filename)
    return r


if __name__ == '__main__':
    app.run(debug=True)
