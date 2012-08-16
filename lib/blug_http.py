"""HTTP server and utilities"""

import os
import io
import os.path
import resource
import select
import socketserver
import socket
from http import server

RUSAGE = """0	{}	time in user mode (float)
{}	time in system mode (float)
{}	maximum resident set size
{}	shared memory size
{}	unshared memory size
{}	unshared stack size
{}	page faults not requiring I/O
{}	page faults requiring I/O
{}	number of swap outs
{}	block input operations
{}	block output operations
{}	messages sent
{}	messages received
{}	signals received
{}	voluntary context switches
{}	involuntary context switches"""

EOL1 = b'\r\n'
EOL2 = b'\n\n'

"""
serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
serversocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
serversocket.bind(('0.0.0.0', 8080))
serversocket.listen(1)
serversocket.setblocking(0)

epoll = select.epoll()
epoll.register(serversocket.fileno(), select.EPOLLIN)

try:
   connections = {}; requests = {}; responses = {}
   while True:
      events = epoll.poll(1)
      for fileno, event in events:
         if fileno == serversocket.fileno():
            connection, address = serversocket.accept()
            connection.setblocking(0)
            epoll.register(connection.fileno(), select.EPOLLIN)
            connections[connection.fileno()] = connection
            requests[connection.fileno()] = b''
            responses[connection.fileno()] = response
         elif event & select.EPOLLIN:
            requests[fileno] += connections[fileno].recv(1024)
            if EOL1 in requests[fileno] or EOL2 in requests[fileno]:
               epoll.modify(fileno, select.EPOLLOUT)
               print('-'*40 + '\n' + requests[fileno].decode()[:-2])
         elif event & select.EPOLLOUT:
            byteswritten = connections[fileno].send(responses[fileno])
            responses[fileno] = responses[fileno][byteswritten:]
            if len(responses[fileno]) == 0:
               epoll.modify(fileno, 0)
               connections[fileno].shutdown(socket.SHUT_RDWR)
         elif event & select.EPOLLHUP:
            epoll.unregister(fileno)
            connections[fileno].close()
            del connections[fileno]
finally:
   epoll.unregister(serversocket.fileno())
   epoll.close()
   serversocket.close()
"""


class EPollMixin:
    """Mixin for socketserver.BaseServer to use epoll instead of select"""

    def server_activate(self):
        """Increase the request_queue_size and set non-blocking"""
        self.connections = dict()
        self.requests = dict()
        self.responses = dict()
        self.addresses = dict()
        self.socket.listen(75)
        self.socket.setblocking(0)
        self.epoll = select.epoll()
        self.epoll.register(self.fileno(), select.EPOLLIN)

    def finish_request(self, request, client_address):
        """Finish one request by instantiating RequestHandlerClass."""
        print ('finish_request')
        self.RequestHandlerClass(request, client_address, self, client_address)

    def handle_request(self):
        """Handle one request, possibly blocking. Does NOT respect timeout.

        To avoid the overhead of select with many client connections,
        use epoll (and later do the same for kqpoll)"""
        events = self.epoll.poll()
        for fd, event in events:
            print ('events start')
            if fd == self.fileno():
                connection, address = self.socket.accept()
                connection.setblocking(0)
                print ('accepting fd {}'.format(connection.fileno()))
                self.epoll.register(connection.fileno(), select.EPOLLIN)
                self.connections[connection.fileno()] = connection
                self.requests[connection.fileno()] = bytes()
                self.responses[connection.fileno()] = io.BytesIO()
                self.addresses[connection.fileno()] = address
            elif event & select.EPOLLIN:
                print ('data to read')
                self.requests[fd] += self.connections[fd].recv(1024)
                if EOL1 in self.requests[fd] or EOL2 in self.requests[fd]:
                    print ('processing request')
                    self.process_request(self.requests[fd], fd)
                    self.epoll.modify(fd, select.EPOLLOUT)
            elif event & select.EPOLLOUT:
                print ('data to write')
                print (self.responses[fd])
                byteswritten = self.connections[fd].send(self.responses[fd].getvalue())
                print ('wrote {} bytes'.format(byteswritten))
                self.responses[fd] = self.responses[fd].getbuffer()[byteswritten:]
                if len(self.responses[fd]) == 0:
                    print ('closing fd')
                    self.epoll.modify(fd, 0)
                    self.connections[fd].shutdown(socket.SHUT_WR)
            elif event & select.EPOLLHUP:
                print ('closing fd epollhup')
                self.epoll.unregister(fd)
                self.connections[fd].close()
                del self.connections[fd]

    def shutdown_request(self, request):
        pass


class EPollRequestHandlerMixin():
    def setup(self):
        self.rfile = io.BytesIO(self.server.requests[self.fd])
        self.wfile = self.server.responses[self.fd]

    def __init__(self, request, client_address, server, fd):
        self.request = request
        self.server = server
        self.connection = self.server.connections[fd]
        self.client_address = self.server.addresses[fd]
        self.fd = fd
        self.setup()
        try:
            self.handle()
        finally:
            self.finish()

    def finish(self):
        self.rfile.close()



class EPollTCPServer(EPollMixin, socketserver.TCPServer):
    pass


class EPollRequestHandler(EPollRequestHandlerMixin, server.SimpleHTTPRequestHandler):
    pass


class BlugHttpServer(EPollTCPServer):
    """epoll based http server"""
    def server_bind(self):
        """Override server_bind to store the server name."""
        socketserver.TCPServer.server_bind(self)
        host, port = self.socket.getsockname()[:2]
        self.server_name = socket.getfqdn(host)
        self.server_port = port


def start_server(host='localhost', port=8000,
        handler_class=server.SimpleHTTPRequestHandler):
    address = (host, port)
    http_server = BlugHttpServer(address, handler_class)
    http_server.serve_forever()


class FileCache():
    """An in-memory cache of static files"""

    FILE_TYPES = ['.html', '.js', '.css']

    def __init__(self, base, debug=0):
        self.base = os.path.normpath(base)
        self.cache = dict()
        self._debug = debug
        self.build_cache(self.base)

    def build_cache(self, base_dir):
        for name in os.listdir(base_dir):
            name = os.path.join(base_dir, name)
            if not os.path.splitext(name)[1] in self.FILE_TYPES:
                if os.path.isdir(name):
                    self.build_cache(name)
            else:
                with open(name, 'r') as input_file:
                    self.cache[name] = input_file.read()

        if self._debug >= 1:
            self._get_cache_stats()

    def _get_cache_stats(self):
        """Returns statistics of the current cache"""
        stat_list = list()
        for filename, file_buffer in self.cache.items():
            path, name = os.path.split(filename)
            stat_list.append(('{name}: {size} B ({path}'.format(
                name=name,
                path=path,
                size=len(self.cache[filename]))))
        return stat_list

    def __str__(self):
        return '\n'.join(self._get_cache_stats())


def print_usage_stats(rusage_struct):
    return  RUSAGE.format(rusage_struct.ru_utime, rusage_struct.ru_stime,
    rusage_struct.ru_maxrss, rusage_struct.ru_ixrss, rusage_struct.ru_idrss,
    rusage_struct.ru_isrss, rusage_struct.ru_minflt, rusage_struct.ru_majflt,
    rusage_struct.ru_nswap, rusage_struct.ru_inblock, rusage_struct.ru_oublock,
    rusage_struct.ru_msgsnd, rusage_struct.ru_msgrcv,
    rusage_struct.ru_nsignals, rusage_struct.ru_nvcsw,
    rusage_struct.ru_nivcsw)

if __name__ == '__main__':
    start_server()
    #cache = FileCache('/home/jeff/code/blug/generated/', 1)
    #print (cache)
    #usage = resource.getrusage(resource.RUSAGE_SELF)
    #print (print_usage_stats(usage))
