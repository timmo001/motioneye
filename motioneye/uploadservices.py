
# Copyright (c) 2013 Calin Crisan
# This file is part of motionEye.
#
# motionEye is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import json
import logging
import mimetypes
import os.path
import time
import urllib
import urllib2 

import settings
import utils


_STATE_FILE_NAME = 'uploadservices.json'
_services = None


class UploadService(object):
    MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1GB
    
    NAME = 'base'

    def __init__(self, camera_id, **kwargs):
        self.camera_id = camera_id

    def __str__(self):
        return self.NAME
    
    @classmethod
    def get_authorize_url(cls):
        return '/'
    
    def test_access(self):
        return True

    def upload_file(self, target_dir, filename):
        if target_dir:
            target_dir = os.path.realpath(target_dir)
            rel_filename = filename[len(target_dir):]
            
            while rel_filename.startswith('/'):
                rel_filename = rel_filename[1:]

            self.debug('uploading file "%s/%s" to %s' % (target_dir, rel_filename, self))

        else:
            rel_filename = os.path.basename(filename)
            
            self.debug('uploading file "%s" to %s' % (filename, self))
        
        try:
            st = os.stat(filename)
        
        except Exception as e:
            msg = 'failed to open file "%s": %s' % (filename, e)
            self.error(msg)
            raise Exception(msg)
         
        if st.st_size > self.MAX_FILE_SIZE:
            msg = 'file "%s" is too large (%sMB/%sMB)' % (filename, st.st_size / 1024 / 1024, self.MAX_FILE_SIZE / 1024 / 1024)
            self.error(msg)
            raise Exception(msg)

        try:
            f = open(filename)
            
        except Exception as e:
            msg = 'failed to open file "%s": %s' % (filename, e)
            self.error(msg)
            raise Exception(msg)

        data = f.read()
        self.debug('size of "%s" is %.3fMB' % (filename, len(data) / 1024.0 / 1024))
        
        mime_type = mimetypes.guess_type(filename)[0] or 'image/jpeg'
        self.debug('mime type of "%s" is "%s"' % (filename, mime_type))

        self.upload_data(rel_filename, mime_type, data)
        
        self.debug('file "%s" successfully uploaded' % filename)

    def upload_data(self, filename, mime_type, data):
        pass
    
    def dump(self):
        return {}
    
    def load(self, data):
        pass
    
    def save(self):
        services = _load()
        camera_services = services.setdefault(self.camera_id, {})
        camera_services[self.NAME] = self
        
        _save(services)

    def log(self, level, message, **kwargs):
        message = self.NAME + ': ' + message
        
        logging.log(level, message, **kwargs)

    def debug(self, message, **kwargs):
        self.log(logging.DEBUG, message, **kwargs)

    def info(self, message, **kwargs):
        self.log(logging.INFO, message, **kwargs)

    def error(self, message, **kwargs):
        self.log(logging.ERROR, message, **kwargs)
        
    @staticmethod
    def get_service_classes():
        return {c.NAME: c for c in UploadService.__subclasses__()}


class GoogleDrive(UploadService):
    NAME = 'gdrive'
    
    AUTH_URL = 'https://accounts.google.com/o/oauth2/auth'
    TOKEN_URL = 'https://accounts.google.com/o/oauth2/token'

    CLIENT_ID = '349038943026-m16svdadjrqc0c449u4qv71v1m1niu5o.apps.googleusercontent.com'
    CLIENT_NOT_SO_SECRET = 'jjqbWmICpA0GvbhsJB3okX7s'
    
    SCOPE = 'https://www.googleapis.com/auth/drive'
    CHILDREN_URL = 'https://www.googleapis.com/drive/v2/files/%(parent_id)s/children?q=%(query)s'
    CHILDREN_QUERY = "'%(parent_id)s' in parents and title = '%(child_name)s' and trashed = false"
    UPLOAD_URL = 'https://www.googleapis.com/upload/drive/v2/files?uploadType=multipart'
    CREATE_FOLDER_URL = 'https://www.googleapis.com/drive/v2/files'

    BOUNDARY = 'motioneye_multipart_boundary'
    
    FOLDER_ID_LIFE_TIME = 300 # 5 minutes

    def __init__(self, camera_id, location=None, authorization_key=None, credentials=None, **kwargs):
        self._location = location
        self._authorization_key = authorization_key
        self._credentials = credentials
        self._folder_ids = {}
        self._folder_id_times = {}
        
        UploadService.__init__(self, camera_id)
    
    @classmethod
    def get_authorize_url(cls):
        query = {
            'scope': cls.SCOPE,
            'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob',
            'response_type': 'code',
            'client_id': cls.CLIENT_ID,
            'access_type': 'offline'
        }

        return cls.AUTH_URL + '?' + urllib.urlencode(query)

    def test_access(self):
        try:
            self._folder_ids = {}
            self._get_folder_id()
            return True

        except Exception as e:
            return str(e)

    def upload_data(self, filename, mime_type, data):
        path = os.path.dirname(filename)
        filename = os.path.basename(filename)

        metadata = {
            'title': filename,
            'parents': [{'id': self._get_folder_id(path)}]
        }

        body = ['--' + self.BOUNDARY]
        body.append('Content-Type: application/json; charset=UTF-8')
        body.append('')
        body.append(json.dumps(metadata))
        body.append('')
        
        body.append('--' + self.BOUNDARY)
        body.append('Content-Type: %s' % mime_type)
        body.append('')
        body.append('')
        body = '\r\n'.join(body)
        body += data
        body += '\r\n--%s--' % self.BOUNDARY
        
        headers = {
            'Content-Type': 'multipart/related; boundary="%s"' % self.BOUNDARY,
            'Content-Length': len(body)
        }
        
        self._request(self.UPLOAD_URL, body, headers)

    def dump(self):
        return {
            'location': self._location,
            'credentials': self._credentials,
            'authorization_key': self._authorization_key,
        }

    def load(self, data):
        if data.get('location'):
            self._location = data['location']
            self._folder_ids = {}
        if data.get('authorization_key'):
            self._authorization_key = data['authorization_key']
            self._credentials = None
        if data.get('credentials'):
            self._credentials = data['credentials']

    def _get_folder_id(self, path=''):
        now = time.time()
        
        folder_id = self._folder_ids.get(path)
        folder_id_time = self._folder_id_times.get(path, 0)
        
        location = self._location
        if not location.endswith('/'):
            location += '/'
        
        location += path

        if not folder_id or (now - folder_id_time > self.FOLDER_ID_LIFE_TIME):
            self.debug('finding folder id for location "%s"' % location)
            folder_id = self._get_folder_id_by_path(location)
            
            self._folder_ids[path] = folder_id
            self._folder_id_times[path] = now

        return folder_id

    def _get_folder_id_by_path(self, path):
        if path and path != '/':
            path = [p.strip() for p in path.split('/') if p.strip()]
            parent_id = 'root'
            for name in path:
                parent_id = self._get_folder_id_by_name(parent_id, name)
        
            return parent_id

        else: # root folder
            return self._get_folder_id_by_name(None, 'root')

    def _get_folder_id_by_name(self, parent_id, child_name, create=True):
        if parent_id:
            query = self.CHILDREN_QUERY % {'parent_id': parent_id, 'child_name': child_name}
            query = urllib.quote(query)
            
        else:
            query = ''
            
        parent_id = parent_id or 'root'
        # when requesting the id of the root folder, we perform a dummy request,
        # event though we already know the id (which is "root"), to test the request 

        url = self.CHILDREN_URL % {'parent_id': parent_id, 'query': query}
        response = self._request(url)
        try:
            response = json.loads(response)

        except Exception:
            self.error("response doesn't seem to be a valid json")
            raise
        
        if parent_id == 'root' and child_name == 'root':
            return 'root'

        items = response.get('items')
        if not items:
            if create:
                self.debug('folder with name "%s" does not exist, creating it' % child_name)
                self._create_folder(parent_id, child_name)
                return self._get_folder_id_by_name(parent_id, child_name, create=False)
            
            else:
                msg = 'folder with name "%s" does not exist' % child_name
                self.error(msg)
                raise Exception(msg)

        return items[0]['id']
    
    def _create_folder(self, parent_id, child_name):
        metadata = {
            'title': child_name,
            'parents': [{'id': parent_id}],
            'mimeType': 'application/vnd.google-apps.folder'
        }

        body = json.dumps(metadata)

        headers = {
            'Content-Type': 'application/json; charset=UTF-8'
        }

        self._request(self.CREATE_FOLDER_URL, body, headers)

    def _request(self, url, body=None, headers=None, retry_auth=True):
        if not self._credentials:
            if not self._authorization_key:
                msg = 'missing authorization key'
                self.error(msg)
                raise Exception(msg)

            self.debug('requesting credentials')
            try:
                self._credentials = self._request_credentials(self._authorization_key)
                self.save()
            
            except Exception as e:
                self.error('failed to obtain credentials: %s' % e)
                raise

        headers = headers or {}
        headers['Authorization'] = 'Bearer %s' % self._credentials['access_token']
        
        self.debug('requesting %s' % url)
        request = urllib2.Request(url, data=body, headers=headers)
        try:
            response = utils.urlopen(request)
        
        except urllib2.HTTPError as e:
            if e.code == 401 and retry_auth: # unauthorized, access token may have expired
                try:
                    self.debug('credentials have probably expired, refreshing them')
                    self._credentials = self._refresh_credentials(self._credentials['refresh_token'])
                    self.save()
                    
                    # retry the request with refreshed credentials
                    return self._request(url, body, headers, retry_auth=False)

                except Exception as e:
                    self.error('refreshing credentials failed')
                    raise
                
            else:
                try:
                    e = json.load(e)
                    msg = e['error']['message']
                
                except Exception:
                    msg = str(e)
                    
                self.error('request failed: %s' % msg)
                raise Exception(msg)

        except Exception as e:
            self.error('request failed: %s' % e)
            raise

        return response.read()
    
    def _request_credentials(self, authorization_key):
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        body = {
            'code': authorization_key,
            'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob',
            'client_id': self.CLIENT_ID, 
            'client_secret': self.CLIENT_NOT_SO_SECRET,
            'scope': self.SCOPE,
            'grant_type': 'authorization_code'
        }
        body = urllib.urlencode(body)

        request = urllib2.Request(self.TOKEN_URL, data=body, headers=headers)
        
        try:
            response = utils.urlopen(request)
        
        except urllib2.HTTPError as e:
            error = json.load(e)
            raise Exception(error.get('error_description') or error.get('error') or str(e))
        
        data = json.load(response)
        
        return {
            'access_token': data['access_token'],
            'refresh_token': data['refresh_token']
        }
    
    def _refresh_credentials(self, refresh_token):
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        body = {
            'refresh_token': refresh_token,
            'client_id': self.CLIENT_ID, 
            'client_secret': self.CLIENT_NOT_SO_SECRET,
            'grant_type': 'refresh_token'
        }
        body = urllib.urlencode(body)

        request = urllib2.Request(self.TOKEN_URL, data=body, headers=headers)
        
        try:
            response = utils.urlopen(request)
        
        except urllib2.HTTPError as e:
            error = json.load(e)
            raise Exception(error.get('error_description') or error.get('error') or str(e))

        data = json.load(response)
        
        return {
            'access_token': data['access_token'],
            'refresh_token': data.get('refresh_token', refresh_token)
        }


class Dropbox(UploadService):
    NAME = 'dropbox'
    
    AUTH_URL = 'https://www.dropbox.com/oauth2/authorize'
    TOKEN_URL = 'https://api.dropboxapi.com/oauth2/token'

    CLIENT_ID = 'dropbox_client_id_placeholder'
    CLIENT_NOT_SO_SECRET = 'dropbox_client_secret_placeholder'
    
    LIST_FOLDER_URL = 'https://api.dropboxapi.com/2/files/list_folder'
    UPLOAD_URL = 'https://content.dropboxapi.com/2/files/upload'

    def __init__(self, camera_id, location=None, authorization_key=None, credentials=None, **kwargs):
        self._location = location
        self._authorization_key = authorization_key
        self._credentials = credentials
        
        UploadService.__init__(self, camera_id)

    @classmethod
    def get_authorize_url(cls):
        query = {
            'response_type': 'code',
            'client_id': cls.CLIENT_ID
        }

        return cls.AUTH_URL + '?' + urllib.urlencode(query)

    def test_access(self):
        body = {
            'path': self._clean_location(),
            'recursive': False,
            'include_media_info': False,
            'include_deleted': False
        }
        
        body = json.dumps(body)
        headers = {'Content-Type': 'application/json'}
        
        try:
            self._request(self.LIST_FOLDER_URL, body, headers)
            return True

        except Exception as e:
            msg = str(e)
            
            # remove trailing punctuation
            while msg and not msg[-1].isalnum():
                msg = msg[:-1]
            
            return msg

    def upload_data(self, filename, mime_type, data):
        metadata = {
            'path': os.path.join(self._clean_location(), filename),
            'mode': 'add',
            'autorename': True,
            'mute': False
        }

        headers = {
            'Content-Type': 'application/octet-stream',
            'Dropbox-API-Arg': json.dumps(metadata)
        }

        self._request(self.UPLOAD_URL, data, headers)

    def dump(self):
        return {
            'location': self._location,
            'credentials': self._credentials,
            'authorization_key': self._authorization_key
        }

    def load(self, data):
        if data.get('location'):
            self._location = data['location']
        if data.get('authorization_key'):
            self._authorization_key = data['authorization_key']
            self._credentials = None
        if data.get('credentials'):
            self._credentials = data['credentials']
    
    def _clean_location(self):
        location = self._location
        if location == '/':
            return ''

        if not location.startswith('/'):
            location = '/' + location
        
        return location

    def _request(self, url, body=None, headers=None, retry_auth=True):
        if not self._credentials:
            if not self._authorization_key:
                msg = 'missing authorization key'
                self.error(msg)
                raise Exception(msg)

            self.debug('requesting credentials')
            try:
                self._credentials = self._request_credentials(self._authorization_key)
                self.save()
            
            except Exception as e:
                self.error('failed to obtain credentials: %s' % e)
                raise

        headers = headers or {}
        headers['Authorization'] = 'Bearer %s' % self._credentials['access_token']
        
        self.debug('requesting %s' % url)
        request = urllib2.Request(url, data=body, headers=headers)
        try:
            response = utils.urlopen(request)
        
        except urllib2.HTTPError as e:
            if e.code == 401 and retry_auth: # unauthorized, access token may have expired
                try:
                    self.debug('credentials have probably expired, refreshing them')
                    self._credentials = self._request_credentials(self._authorization_key)
                    self.save()

                    # retry the request with refreshed credentials
                    self._request(url, body, headers, retry_auth=False)

                except Exception as e:
                    self.error('refreshing credentials failed')
                    raise
            
            elif str(e).count('not_found'):
                msg = 'folder "%s" not found' % self._location
                self.error(msg)
                raise Exception(msg)
            
            else:
                self.error('request failed: %s' % e)
                raise

        except Exception as e:
            self.error('request failed: %s' % e)
            raise

        return response.read()
    
    def _request_credentials(self, authorization_key):
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        body = {
            'code': authorization_key,
            'client_id': self.CLIENT_ID, 
            'client_secret': self.CLIENT_NOT_SO_SECRET,
            'grant_type': 'authorization_code'
        }
        body = urllib.urlencode(body)

        request = urllib2.Request(self.TOKEN_URL, data=body, headers=headers)
        
        try:
            response = utils.urlopen(request)
        
        except urllib2.HTTPError as e:
            error = json.load(e)
            raise Exception(error.get('error_description') or error.get('error') or str(e))
        
        data = json.load(response)
        
        return {
            'access_token': data['access_token']
        }


def get_authorize_url(service_name):
    cls = UploadService.get_service_classes().get(service_name)

    if cls:
        return cls.get_authorize_url()
    
    else:
        return None


def get(camera_id, service_name):
    global _services
    
    if _services is None:
        _services = _load()
        
    camera_id = str(camera_id)

    service = _services.get(camera_id, {}).get(service_name)
    if service is None:
        cls = UploadService.get_service_classes().get(service_name)
        if cls:
            service = cls(camera_id=camera_id)
            _services.setdefault(camera_id, {})[service_name] = service

            logging.debug('created default upload service "%s" for camera with id "%s"' % (service_name, camera_id))
    
    return service


def test_access(camera_id, service_name, data):
    logging.debug('testing access to %s' % service_name)

    service = get(camera_id, service_name)
    service.load(data)
    if not service:
        return 'unknown upload service %s' % service_name

    return service.test_access()


def update(camera_id, service_name, settings):
    service = get(camera_id, service_name)
    service.load(settings)
    service.save()


def upload_media_file(camera_id, target_dir, service_name, filename):
    service = get(camera_id, service_name)
    if not service:
        return logging.error('service "%s" not initialized for camera with id %s' % (service_name, camera_id))

    try:
        service.upload_file(target_dir, filename)

    except Exception as e:
        logging.error('failed to upload file "%s" with service %s: %s' % (filename, service, e), exc_info=True)


def _load():
    services = {}
    
    file_path = os.path.join(settings.CONF_PATH, _STATE_FILE_NAME)
    
    if os.path.exists(file_path):
        logging.debug('loading upload services state from "%s"...' % file_path)
    
        try:
            file = open(file_path, 'r')
        
        except Exception as e:
            logging.error('could not open upload services state file "%s": %s' % (file_path, e))
            
            return

        try:
            data = json.load(file)

        except Exception as e:
            return logging.error('could not read upload services state from file "%s": %s' % (file_path, e))

        finally:
            file.close()

        for camera_id, d in data.iteritems():
            for name, state in d.iteritems():
                camera_services = services.setdefault(camera_id, {})
                cls = UploadService.get_service_classes().get(name)
                if cls:
                    service = cls(camera_id=camera_id)
                    service.load(state)

                    camera_services[name] = service
    
                    logging.debug('loaded upload service "%s" for camera with id "%s"' % (name, camera_id))
    
    return services


def _save(services):
    file_path = os.path.join(settings.CONF_PATH, _STATE_FILE_NAME)
    
    logging.debug('saving upload services state to "%s"...' % file_path)

    try:
        file = open(file_path, 'w')

    except Exception as e:
        logging.error('could not open upload services state file "%s": %s' % (file_path, e))
        
        return
    
    data = {}
    for camera_id, camera_services in services.iteritems():
        for name, service in camera_services.iteritems():
            data.setdefault(str(camera_id), {})[name] = service.dump()

    try:
        json.dump(data, file, sort_keys=True, indent=4)

    except Exception as e:
        logging.error('could not save upload services state to file "%s": %s'(file_path, e))

    finally:
        file.close()


