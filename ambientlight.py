import argparse
import collections
import re

import os
import signal
import sys
import time
import termios
import threading
import tty

import RPi.GPIO

import BaseHTTPServer
import SimpleHTTPServer
import json
import urlparse



class UnbufferedInput(object):

    def start(self):

        self.prev_tty_attributes = termios.tcgetattr(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())


    def get(self):

        return sys.stdin.read(1)


    def stop(self):

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.prev_tty_attributes)


    def __enter__(self):

        self.start()
        return self


    def __exit__(self, exception_type, exception_value, traceback):

        self.stop()
        return False



class Light(object):


    def size(self):

        return 0


    def pixels(self):

        return []


    def set(self, pixel_index, pixel_color):

        pass


    def show(self):

        pass



class TimingLight(Light):

    def __init__(self, strip_size, write_stream = sys.stdout, write_prefix = '', write_suffix = ''):

        self.strip_size = strip_size
        self.strip_colors = [(0,0,0) for pixel_index in xrange(strip_size)]

        self.write_stream = write_stream
        self.write_prefix = write_prefix
        self.write_suffix = write_suffix

        self.last_show_time = None


    def size(self):

        return self.strip_size


    def pixels(self):

        for pixel_index in xrange(self.strip_size):
            yield (pixel_index, self.strip_colors[pixel_index])


    def set(self, pixel_index, pixel_color):

        self.strip_colors[pixel_index] = pixel_color


    def show(self):

        if not self.last_show_time:
            self.last_show_time = time.time()
        else:
            delta_show_time = time.time() - self.last_show_time

            if delta_show_time > 0.5:
                self.last_show_time = None
            else:
                write_stream = self.write_stream
                write_stream.write(self.write_prefix)
                write_stream.write('%.0f ms = %.1f/s' % (delta_show_time * 1000.0, 1.0 / delta_show_time))
                write_stream.write(self.write_suffix)
                write_stream.flush()

                self.last_show_time = time.time()




class DebugLight(Light):

    channel_weights = (0.2989, 0.5870, 0.1140)
    interpolated_char = (u'\u0020', u'\u2591', u'\u2592', u'\u2593')


    def __init__(self, strip_size, write_stream = sys.stdout, write_prefix = '', write_suffix = ''):

        self.strip_size = strip_size
        self.strip_colors = [(0,0,0) for pixel_index in xrange(strip_size)]

        self.write_stream = write_stream
        self.write_prefix = write_prefix
        self.write_suffix = write_suffix


    def size(self):

        return self.strip_size


    def pixels(self):

        for pixel_index in xrange(self.strip_size):
            yield (pixel_index, self.strip_colors[pixel_index])


    def set(self, pixel_index, pixel_color):

        self.strip_colors[pixel_index] = pixel_color


    def show(self):

        write_stream = self.write_stream
        write_stream.write(self.write_prefix)

        for pixel_index, pixel_color in self.pixels():
            pixel_color_coord = [channel * 5.0/255.0 for channel in pixel_color]
            pixel_color_index = [int(channel_coord + 0.5) for channel_coord in pixel_color_coord]

            weighted_deviation = [self.channel_weights[channel_index] * abs(pixel_color_coord[channel_index] - pixel_color_index[channel_index]) for channel_index in xrange(len(pixel_color))]

            interpolated_channel_index = max(xrange(len(pixel_color)), key = lambda channel_index: weighted_deviation[channel_index])
            interpolated_channel_offset = pixel_color_coord[interpolated_channel_index] - pixel_color_index[interpolated_channel_index]

            interpolated_char_index = int(interpolated_channel_offset * len(self.interpolated_char))

            if interpolated_char_index == 0:
                background_color_slot = 16 + 36 * pixel_color_index[0] + 6 * pixel_color_index[1] + pixel_color_index[2]
                write_stream.write('\033[48;5;%dm%s' % (background_color_slot, self.interpolated_char[interpolated_char_index]))
            else:
                pixel_color_index[interpolated_channel_index] = int(pixel_color_coord[interpolated_channel_index])
                background_color_slot = 16 + 36 * pixel_color_index[0] + 6 * pixel_color_index[1] + pixel_color_index[2]
                pixel_color_index[interpolated_channel_index] += 1
                foreground_color_slot = 16 + 36 * pixel_color_index[0] + 6 * pixel_color_index[1] + pixel_color_index[2]
                write_stream.write('\033[38;5;%dm\033[48;5;%dm%s' % (foreground_color_slot, background_color_slot, self.interpolated_char[interpolated_char_index]))

        write_stream.write('\033[m')
        write_stream.write(self.write_suffix)
        write_stream.flush()




class NeopixelLight(Light):

    strip_freq_hz    = 800000
    strip_dma        = 10
    strip_invert     = False
    strip_channel    = 0
    strip_type_name  = 'WS2811'
    strip_type_order = 'GRB'


    def __init__(self, strip_size, strip_pin, strip_brightness = 255):

        import neopixel

        self.strip_size       = strip_size
        self.strip_pin        = strip_pin
        self.strip_brightness = strip_brightness

        self.strip_type = getattr(neopixel.ws, '%s_STRIP_%s' % (self.strip_type_name, self.strip_type_order))

        self.strip = neopixel.Adafruit_NeoPixel(
            self.strip_size,
            self.strip_pin,
            self.strip_freq_hz,
            self.strip_dma,
            self.strip_invert,
            self.strip_brightness,
            self.strip_channel,
            self.strip_type)

        self.strip.begin()


    def size(self):

        return self.strip_size


    def pixels(self):

        for pixel_index in xrange(self.strip_size):
            pixel_color_value = self.strip.getPixelColor(pixel_index)

            red   = ((pixel_color_value >> 16) & 0xFF)
            green = ((pixel_color_value >>  8) & 0xFF)
            blue  = ( pixel_color_value        & 0xFF)

            yield (pixel_index, (red, green, blue))


    def set(self, pixel_index, pixel_color):

        red   = (pixel_color[0] & 0xFF)
        green = (pixel_color[1] & 0xFF)
        blue  = (pixel_color[2] & 0xFF)

        self.strip.setPixelColor(pixel_index, (red << 16) | (green << 8) | blue)


    def show(self):

        self.strip.show()



class LightController(object):

    class Layer(object):

        def __init__(self, pixel_length, pixel_color, pixel_alpha_left, pixel_alpha_right, pixel_offset, pixel_offset_speed):

            self.pixel_offset       = pixel_offset
            self.pixel_offset_speed = pixel_offset_speed

            if pixel_length == 1:
                self.pixel_alpha_colors = [
                    (pixel_alpha_left if pixel_offset_speed > 0.0 else pixel_alpha_right,) + pixel_color]

            else:
                pixel_alpha_gradient = (pixel_alpha_right - pixel_alpha_left) / (pixel_length - 1)

                self.pixel_alpha_colors = [
                    (pixel_alpha_left + pixel_alpha_gradient * pixel_index,) + pixel_color
                        for pixel_index
                        in xrange(pixel_length)]



    def __init__(self, light, step_rate = 0.0):

        self.light = light
        self.light_color = (0,0,0)

        self.step_rate = step_rate

        self.layers = []
        self.layer_base_color = self.light_color
        self.layer_thread = None
        self.layer_thread_running = False

        self.layer_condition_wait_step     = threading.Condition()
        self.layer_condition_access_layers = threading.Condition()


    def start(self):

        if not self.layer_thread:
            self.layer_base_color = self.light_color

            self.layer_thread_running = True
            self.layer_thread = threading.Thread(target = self.layer_thread_proc)
            self.layer_thread.start()


    def stop(self):

        if self.layer_thread:
            self.layer_thread_running = False

            self.layer_condition_wait_step.acquire()
            self.layer_condition_wait_step.notify()
            self.layer_condition_wait_step.release()

            self.layer_thread.join()
            self.layer_thread = None


    def layer_thread_proc(self):

        range_length_left  = (self.light.strip_size + 1) // 2
        range_length_right =  self.light.strip_size      // 2

        prev_step_time = time.time()
        min_step_duration = (1.0 / self.step_rate if self.step_rate > 0.0 else 0.0)

        while self.layer_thread_running:
            pixel_colors = None

            self.layer_condition_access_layers.acquire()

            if self.layers:
                curr_step_time = time.time()
                delta_step_time = curr_step_time - prev_step_time
                prev_step_time = curr_step_time

                for layer in self.layers:
                    layer.pixel_offset += layer.pixel_offset_speed * delta_step_time

                while self.layers:
                    layer = self.layers[0]

                    if layer.pixel_offset_speed < 0.0:
                        # layer moves right to left
                        if layer.pixel_offset + len(layer.pixel_alpha_colors) > 0.0:
                            # right-hand part of layer visible on left-hand side
                            break
                        else:
                            # final color at right end
                            self.layer_base_color = layer.pixel_alpha_colors[-1][1:]
                            self.layers.pop(0)
                    else:
                        # layer moves left to right
                        if layer.pixel_offset < range_length_left:
                            # left-hand part of layer visible on right-hand side
                            break
                        else:
                            # layer moves left to right, final color at left end
                            self.layer_base_color = layer.pixel_alpha_colors[0][1:]
                            self.layers.pop(0)

                pixel_colors = [
                    tuple(int(channel)
                        for channel
                        in reduce(  # -> (R,G,B)
                            lambda pixel_base_color, layer: (  # -> (R,G,B)
                                lambda layer_pixel_alpha_color:  # -> (R,G,B)
                                    tuple((1.0 - layer_pixel_alpha_color[0]) * pixel_base_color       [channel_index] +
                                                 layer_pixel_alpha_color[0]  * layer_pixel_alpha_color[channel_index+1]
                                        for channel_index
                                        in xrange(len(pixel_base_color))))((
                                lambda clamped_pixel_index: (  # -> (A,R,G,B)
                                    layer.pixel_alpha_colors[int(clamped_pixel_index)]
                                        if
                                            clamped_pixel_index == int(clamped_pixel_index)
                                        else
                                    tuple((1.0 - (clamped_pixel_index - int(clamped_pixel_index))) * channel_left +
                                                 (clamped_pixel_index - int(clamped_pixel_index))  * channel_right
                                        for
                                            channel_left,
                                            channel_right
                                        in zip(
                                            layer.pixel_alpha_colors[int(clamped_pixel_index)],
                                            layer.pixel_alpha_colors[int(clamped_pixel_index)+1]))))(
                                max(0.0,
                                min(len(layer.pixel_alpha_colors)-1.0,
                                pixel_index - layer.pixel_offset)))),
                            self.layers,
                            self.layer_base_color))
                    for pixel_index
                    in xrange(range_length_left)]

            self.layer_condition_access_layers.release()

            if pixel_colors:
                pixel_colors += pixel_colors[range_length_right-1::-1]
                for pixel_index in xrange(len(pixel_colors)):
                    self.light.set(pixel_index, pixel_colors[pixel_index])
                self.light.show()

            if self.layer_thread_running:
                if self.layers:
                    # sleep for remainder of allocated step time
                    self.layer_condition_wait_step.acquire()
                    remaining_step_duration = min_step_duration - (time.time() - curr_step_time)
                    if remaining_step_duration > 0.0:
                        self.layer_condition_wait_step.wait(remaining_step_duration)
                    self.layer_condition_wait_step.release()
                else:
                    # sleep until awakened
                    self.layer_condition_wait_step.acquire()
                    self.layer_condition_wait_step.wait()
                    self.layer_condition_wait_step.release()

                    # restart step timing (otherwise next step delta includes sleep)
                    prev_step_time = time.time()


    def set_light_color(self, light_color, transition_time = 0.0):

        print 'Set light color to (%d,%d,%d)' % light_color

        if transition_time <= 0.0:
            # dummy layer that is instantly complete
            layer = self.Layer(
                pixel_length       = 1,
                pixel_color        = light_color,
                pixel_alpha_left   = 1.0,
                pixel_alpha_right  = 1.0,
                pixel_offset       = -1.0,
                pixel_offset_speed = -1.0)

        else:
            range_length_left = (self.light.strip_size + 1) // 2

            channel_weights = (0.2989, 0.5870, 0.1140)
            old_light_color_lightness = sum(channel * channel_weight for channel, channel_weight in zip(self.light_color, channel_weights))
            new_light_color_lightness = sum(channel * channel_weight for channel, channel_weight in zip(     light_color, channel_weights))

            if new_light_color_lightness > old_light_color_lightness:
                # fade in brighter colors from center to outside
                layer = self.Layer(
                    pixel_length       = range_length_left,
                    pixel_color        = light_color,
                    pixel_alpha_left   = 0.0,
                    pixel_alpha_right  = 1.0,
                    pixel_offset       = range_length_left,
                    pixel_offset_speed = -range_length_left / float(transition_time))
            else:
                # fade in darker colors from outside to center
                layer = self.Layer(
                    pixel_length       = range_length_left,
                    pixel_color        = light_color,
                    pixel_alpha_left   = 1.0,
                    pixel_alpha_right  = 0.0,
                    pixel_offset       = -range_length_left,
                    pixel_offset_speed = range_length_left / float(transition_time))

        self.layer_condition_access_layers.acquire()

        self.layers += [layer]
        self.light_color = light_color

        if len(self.layers) == 1:
            self.layer_condition_wait_step.acquire()
            self.layer_condition_wait_step.notify()
            self.layer_condition_wait_step.release()

        self.layer_condition_access_layers.release()



class ControlHTTPRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):

    def do_GET(self):

        if self.path == '/light':
            self.do_get_light()
        else:
            self.do_get_web(self.path)


    def do_POST(self):

        request_body_length = int(self.headers['Content-Length'])
        request_body = self.rfile.read(request_body_length)
        request_args = urlparse.parse_qs(request_body, keep_blank_values = True)

        if self.path == '/light':
            self.do_set_light(request_args)
        else:
            self.send_response(code = 404)

    
    def do_get_light(self):

        self.send_response(code = 200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()

        self.wfile.write(
            json.dumps(collections.OrderedDict((
                ('r', self.server.light_controller.light_color[0]),
                ('g', self.server.light_controller.light_color[1]),
                ('b', self.server.light_controller.light_color[2]),
            ))))
        self.wfile.write('\n')


    def do_set_light(self, request_args):

        light_color = (
            int(request_args['r'][0]),
            int(request_args['g'][0]),
            int(request_args['b'][0]))

        transition_time = (float(request_args['time'][0]) if 'time' in request_args else 0.0)

        self.server.light_controller.set_light_color(light_color, transition_time)
        self.send_response(code = 200)

    def do_get_web(self, path):

        if path == '/':
            web_file_name = 'index.html'
        else:
            web_file_name = path[1:]

        if not re.match(r'^(([-\w]+\.)*[-\w]+/)*([-\w]+\.)*[-\w]+$', web_file_name):
            self.send_response(code = 404)

        else:
            web_file_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'web', *web_file_name.split('/'))

            try:
                with open(web_file_path, 'r') as web_file:
                    web_file_contents = web_file.read()

                try:
                    web_file_name_ext = os.path.splitext(web_file_name)[1]
                    web_file_type = SimpleHTTPServer.SimpleHTTPRequestHandler.extensions_map[web_file_name_ext]
                except:
                    web_file_type = SimpleHTTPServer.SimpleHTTPRequestHandler.extensions_map['']

                self.send_response(code = 200)
                self.send_header('Content-Type', web_file_type)
                self.send_header('Content-Length', len(web_file_contents))
                self.end_headers()

                self.wfile.write(web_file_contents)

            except IOError:
                self.send_response(code = 404)



# colors:
#
# (255,255,255)   2.46 A   light blue
# (255,160,64)    1.70 A   nice natural white
# (255,80,12)     1.35 A   warm orange


if __name__ == '__main__':

    def argparse_positive_int(arg):
        positive_int = int(arg)
        if positive_int < 1:
            raise argparse.ArgumentTypeError('must be a positive integer')
        return positive_int

    def argparse_ip_hostname(arg):
        ip_hostname = arg;
        if not re.match(r'^([-0-9A-Za-z]{1,63}\.)*[-0-9A-Za-z]{1,63}$', ip_hostname):
            raise argparse.ArgumentTypeError('must be a hostname (an IP address or sequence of labels separated by dots)')
        if len(ip_hostname) > 253:
            raise argparse.ArgumentTypeError('must not be longer than 253 characters')
        return ip_hostname

    def argparse_ip_port(arg):
        ip_port = int(arg)
        if ip_port <= 0:
            raise ArgumentTypeError('must be a positive integer')
        if ip_port > 65535:
            raise ArgumentTypeError('must not be greater than 65535')
        return ip_port

    argparser = argparse.ArgumentParser(description = 'Ambient light control server.')

    argparser.add_argument('--step-rate',      type = argparse_positive_int,                default = 60)
    argparser.add_argument('--light-count',    type = argparse_positive_int,                default = 16)
    argparser.add_argument('--light-driver',   choices = ['neopixel', 'console', 'timing'], default = 'neopixel')
    argparser.add_argument('--server-address', type = argparse_ip_hostname,                 default = None)
    argparser.add_argument('--server-port',    type = argparse_ip_port,                     default = 8000)

    args = argparser.parse_args()

    server_address = (args.server_address or '', args.server_port)
    server = BaseHTTPServer.HTTPServer(server_address, ControlHTTPRequestHandler)

    if args.light_driver == 'neopixel': light = NeopixelLight(strip_size = args.light_count, strip_pin = 18)
    if args.light_driver == 'console':  light = DebugLight   (strip_size = args.light_count, write_prefix = '\r', write_suffix = '\n')
    if args.light_driver == 'timing':   light = TimingLight  (strip_size = args.light_count, write_prefix = '\r', write_suffix = '\n')

    server.light_controller = LightController(light, step_rate = args.step_rate)
    server.light_controller.start()

    def sigint_handler(signal, frame):
        def shutdown_server():
            print 'Initiating server shutdown...'
            server.shutdown()
        shutdown_server_thread = threading.Thread(target = shutdown_server)
        shutdown_server_thread.start()

    signal.signal(signal.SIGINT, sigint_handler)

    print 'Ready to receive HTTP requests on http://%s:%d (press Ctrl+C to exit).' % (server.server_name, server.server_port)

    server.serve_forever(poll_interval = 0.5)
    server.light_controller.stop()

    print 'Exiting.'


# vim:set ts=4 sw=4 et:
