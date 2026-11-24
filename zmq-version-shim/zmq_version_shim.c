/* Workaround for pebbe/zmq4's strict compile-time-vs-runtime libzmq
 * version equality check. Each droneid-go binary was built against a
 * specific libzmq patch version, and refuses to start if the system's
 * runtime libzmq is a different patch (even though same-minor patch
 * bumps are ABI compatible). Preloading this shim makes zmq_version()
 * report the patch version the binary expects (passed via -DZMQ_SHIM_*
 * at build time) while every other ZMQ call still runs against the
 * real, unmodified system libzmq.
 */
#ifndef ZMQ_SHIM_MAJOR
#define ZMQ_SHIM_MAJOR 4
#endif
#ifndef ZMQ_SHIM_MINOR
#define ZMQ_SHIM_MINOR 3
#endif
#ifndef ZMQ_SHIM_PATCH
#define ZMQ_SHIM_PATCH 4
#endif

void zmq_version(int *major, int *minor, int *patch) {
    *major = ZMQ_SHIM_MAJOR;
    *minor = ZMQ_SHIM_MINOR;
    *patch = ZMQ_SHIM_PATCH;
}
