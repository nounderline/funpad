import sys
import inspect
import logging
import threading
import weakref
import watchfiles
import rich
import rich.traceback
import asyncio
import aiohttp.web
import datetime


rich.traceback.install(show_locals=True)


PATH = sys.argv[1] if len(sys.argv) > 1 else "."

RichConsole = rich.console.Console()


logger = logging.Logger(__name__)
logger.setLevel(logging.DEBUG)


def load_module(path: str, *, name="funpad.user.scratch"):
    """
    See: https://stackoverflow.com/questions/67631

    TODO: Use runpy maybe?
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)

    if not spec:
        raise ImportError(f"Could not load module {name} from {path}")

    module = importlib.util.module_from_spec(spec)

    # I don't think this is necesssary
    # May be used only for caching?
    # sys.modules[name] = module

    if not spec.loader:
        raise ImportError(f"Could not load module {name} from {path}")

    spec.loader.exec_module(module)

    return module


class WebServer:
    def __init__(self, runner: "Runner", *, host="127.0.0.1", port=8080):
        self.runner = runner
        self.app = aiohttp.web.Application()
        self.host = host
        self.port = port

        self.setup_routes()

    def setup_routes(self):
        self.app.router.add_get("/", self.GET_home)
        self.app.router.add_get("/locals", self.GET_locals)
        self.app.router.add_get("/events", self.GET_events)

    async def GET_home(self, request):
        return aiohttp.web.json_response({})

    async def GET_locals(self, request):
        return aiohttp.web.json_response({})

    async def GET_events(self, request):
        async def event_stream(response):
            while True:
                data = f"data: {datetime.datetime.now().isoformat()}\n\n"
                await response.write(data.encode("utf-8"))
                await asyncio.sleep(1)

        response = aiohttp.web.StreamResponse(
            status=200, reason="OK", headers={"Content-Type": "text/event-stream"}
        )

        await response.prepare(request)
        await event_stream(response)

        return response

    def _serve(self):
        aiohttp.web.run_app(
            self.app, host=self.host, port=self.port, print=None, handle_signals=False
        )

    def start(self):
        self.thread = threading.Thread(target=self._serve, name="web_server")
        self.thread.daemon = True
        self.thread.start()

        return self


class Runner:
    scrach_module_name = "funpad.user.scratch"
    thread: threading.Thread | None = None
    i = 0
    ready_event = threading.Event()
    base_locals = {}
    local_sources = weakref.WeakKeyDictionary()

    def __init__(self, path, *, locals={}):
        self.locals = locals
        self.path = path

    def _execute(self):
        self._run()

        # self.ready_event.set()

        for _ in watchfiles.watch(self.path):
            self._run()

    def _filter_member(self, key, value, old_value):
        if key.startswith("__"):
            return False

        source = self._get_source(value)

        if source:
            module = getattr(value, "__module__", None)

            # Ignore values from other modules
            if not module or not module.startswith(self.scrach_module_name + "_"):
                return False

            if old_value:
                # always load and execute main
                if key == "main":
                    return True

                try:
                    old_source = self.local_sources.get(old_value)
                    new_source = self._get_source(value)

                    logger.debug("Old source: %s", old_source)
                    logger.debug("New source: %s", new_source)

                    if old_source == new_source:
                        return False

                except TypeError as e:
                    logger.error(e)

        if old_value == value:
            return False

        return True

    def _run(self):
        self.i += 1

        try:
            module_name = self.scrach_module_name + "_" + str(self.i)
            module = load_module(self.path, name=module_name)

            run_locals = inspect.getmembers(module)
        except Exception as e:
            # TODO: print pretty stacktrace
            raise e

            logging.error(e)

        new_locals = {}

        for key, value in run_locals:
            old_value = self.base_locals.get(key)

            if not self._filter_member(key, value, old_value):
                continue

            new_locals[key] = value

        rich.print("New locals:", new_locals)

        for value in new_locals.values():
            source = self._get_source(value)

            try:
                self.local_sources[value] = source
            except TypeError:
                # Handle primitives values which cannot be weakly references
                pass

        self.locals.update(new_locals)

        self.base_locals.update(new_locals)

        main_fn = self.base_locals.get("main")

        if main_fn:
            print("Executing main...", end="")

            try:
                res = main_fn()

                if res:
                    print()
                    rich.print(res)
                else:
                    print(" Done")

            except Exception as e:
                RichConsole.print_exception()

    def start(self):
        self.thread = threading.Thread(target=self._execute, name="script_runner")
        self.thread.daemon = True
        self.thread.start()

        return self

    def _get_source(self, object):
        try:
            source = inspect.getsource(object)
        except TypeError:
            # TODO: consider hashing objects without source
            # like compiled functions
            # source = hash(source)
            source = None

        return source


def start_ipython(local_ns):
    from IPython.terminal.ipapp import TerminalIPythonApp

    app = TerminalIPythonApp(
        argv=[],
        colors="neutral",
        header="None",
        confirm_exit=False,
        user_ns=local_ns,
        display_banner=False,
    )

    app.initialize(argv=[])

    app.shell.run_cell("%pdb", store_history=False)
    app.shell.run_cell("%load_ext rich", store_history=False)

    globals()["ipy"] = app
    app.start()


def main():
    globals()["i"] = rich.inspect

    local_ns = locals()

    runner = Runner(PATH, locals=local_ns)

    host = "127.0.0.1"
    port = 8080
    server = WebServer(runner, host=host, port=port)

    print(f"Web Server listening on http://{host}:{port}")

    runner.start()
    server.start()

    # we start REPL in a main thread
    start_ipython(local_ns)


if __name__ == "__main__":
    main()
