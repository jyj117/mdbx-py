/* Copyright 2026 clibmdbx contributors
 * SPDX-License-Identifier: Apache-2.0
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <structmember.h>

#ifdef Py_GIL_DISABLED
#error "clibmdbx currently requires a GIL-enabled CPython build"
#endif

#include <stdint.h>
#include <string.h>

#ifdef _WIN32
#include <process.h>
#else
#include <unistd.h>
#endif

#include "mdbx.h"

#define CLIBMDBX_VERSION "1.0.1"
#define CLIBMDBX_RELEASE_TAG "v0.14.3"
#define CLIBMDBX_RELEASE_COMMIT "f7a3a9323cacacfa9dc6137ae7a7252a67744ff0"
#define CLIBMDBX_AMALGAMATION_COMMIT "251562b2dc55266d8e6d0e6627ec88ecb410702f"
#define CLIBMDBX_ARCHIVE_SHA256 "dbc4a791c44d3e51a8159eedfee0dedada7b21d46c22588f0fa99294983f33cd"

typedef struct EnvObject EnvObject;
typedef struct TxnObject TxnObject;
typedef struct DbObject DbObject;
typedef struct CursorObject CursorObject;
typedef struct DbState DbState;
typedef struct OrphanTxn OrphanTxn;

/*
 * A DBI belongs to the environment, not to an individual Python Database
 * wrapper.  libmdbx may return the same DBI for repeated opens and explicitly
 * requires that such a handle is closed at most once and never while another
 * transaction can reference it.  Keep one shared state per live DBI and let
 * mdbx_env_close_ex() release ordinary handles.  A state becomes invalid only
 * when libmdbx itself closes the handle (a rolled-back create or drop(del)).
 *
 * Access to this list is protected by the GIL.  creator is a non-owning
 * pointer; every transaction resolves its provisional states before it can be
 * destroyed.
 */
struct DbState {
  DbState *next;
  TxnObject *creator;
  TxnObject *rename_txn;
  PyObject *name;
  PyObject *rename_original;
  MDBX_dbi dbi;
  Py_ssize_t wrappers;
  int valid;
};

struct OrphanTxn {
  OrphanTxn *next;
  MDBX_txn *txn;
  TxnObject *parent;
  unsigned long owner;
};

struct EnvObject {
  PyObject_HEAD
  MDBX_env *env;
  PyObject *path;
  DbState *db_states;
  OrphanTxn *orphaned_txns;
  MDBX_dbi main_dbi;
  long pid;
  Py_ssize_t active_txns;
  Py_ssize_t active_write_txns;
  Py_ssize_t active_operations;
  unsigned long write_owner;
  int closed;
};

struct TxnObject {
  PyObject_HEAD
  MDBX_txn *txn;
  EnvObject *env;
  TxnObject *parent;
  unsigned long owner;
  Py_ssize_t active_children;
  Py_ssize_t open_cursors;
  int has_db_changes;
  int readonly;
  int reset;
  int broken;
  int finished;
};

struct DbObject {
  PyObject_HEAD
  EnvObject *env;
  DbState *state;
  PyObject *name;
  int closed;
};

struct CursorObject {
  PyObject_HEAD
  MDBX_cursor *cursor;
  TxnObject *txn;
  DbObject *db;
  unsigned long owner;
  int positioned;
  int iter_started;
  int closed;
};

static PyTypeObject EnvType = {PyVarObject_HEAD_INIT(NULL, 0)};
static PyTypeObject TxnType = {PyVarObject_HEAD_INIT(NULL, 0)};
static PyTypeObject DbType = {PyVarObject_HEAD_INIT(NULL, 0)};
static PyTypeObject CursorType = {PyVarObject_HEAD_INIT(NULL, 0)};

static PyObject *Error;
static PyObject *KeyExistsError;
static PyObject *NotFoundError;
static PyObject *PageNotFoundError;
static PyObject *MapFullError;
static PyObject *ReadersFullError;
static PyObject *DbsFullError;
static PyObject *TxnFullError;
static PyObject *CursorFullError;
static PyObject *PageFullError;
static PyObject *UnableExtendMapError;
static PyObject *BadTxnError;
static PyObject *BadDbiError;
static PyObject *BadRslotError;
static PyObject *BadValueSizeError;
static PyObject *BusyError;
static PyObject *CorruptedError;
static PyObject *PanicError;
static PyObject *VersionMismatchError;
static PyObject *InvalidError;
static PyObject *IncompatibleError;
static PyObject *ProblemError;
static PyObject *MultiValueError;
static PyObject *BadSignatureError;
static PyObject *WannaRecoveryError;
static PyObject *KeyMismatchError;
static PyObject *TooLargeError;
static PyObject *ThreadError;
static PyObject *TxnOverlappingError;
static PyObject *BacklogDepletedError;
static PyObject *DuplicatedLockError;
static PyObject *DanglingDbiError;
static PyObject *OustedError;
static PyObject *MvccRetardedError;
static PyObject *LaggardReaderError;
static PyObject *ReadonlyError;
static PyObject *InvalidParameterError;
static PyObject *LockError;
static PyObject *NoMemoryError;
static PyObject *DiskError;
static PyObject *ForkError;
static PyObject *ClosedError;

static long current_pid(void) {
#ifdef _WIN32
  return (long)_getpid();
#else
  return (long)getpid();
#endif
}

static unsigned long current_thread(void) { return PyThread_get_thread_ident(); }

static PyObject *exception_for_code(int rc) {
  switch (rc) {
  case MDBX_KEYEXIST:
    return KeyExistsError;
  case MDBX_NOTFOUND:
    return NotFoundError;
  case MDBX_PAGE_NOTFOUND:
    return PageNotFoundError;
  case MDBX_MAP_FULL:
    return MapFullError;
  case MDBX_READERS_FULL:
    return ReadersFullError;
  case MDBX_DBS_FULL:
    return DbsFullError;
  case MDBX_TXN_FULL:
    return TxnFullError;
  case MDBX_CURSOR_FULL:
    return CursorFullError;
  case MDBX_PAGE_FULL:
    return PageFullError;
  case MDBX_UNABLE_EXTEND_MAPSIZE:
    return UnableExtendMapError;
  case MDBX_BAD_TXN:
    return BadTxnError;
  case MDBX_BAD_DBI:
    return BadDbiError;
  case MDBX_BAD_RSLOT:
    return BadRslotError;
  case MDBX_BAD_VALSIZE:
    return BadValueSizeError;
  case MDBX_BUSY:
    return BusyError;
  case MDBX_CORRUPTED:
    return CorruptedError;
  case MDBX_PANIC:
    return PanicError;
  case MDBX_VERSION_MISMATCH:
    return VersionMismatchError;
  case MDBX_INVALID:
    return InvalidError;
  case MDBX_INCOMPATIBLE:
    return IncompatibleError;
  case MDBX_PROBLEM:
    return ProblemError;
  case MDBX_EMULTIVAL:
    return MultiValueError;
  case MDBX_EBADSIGN:
    return BadSignatureError;
  case MDBX_WANNA_RECOVERY:
    return WannaRecoveryError;
  case MDBX_EKEYMISMATCH:
    return KeyMismatchError;
  case MDBX_TOO_LARGE:
    return TooLargeError;
  case MDBX_THREAD_MISMATCH:
    return ThreadError;
  case MDBX_TXN_OVERLAPPING:
    return TxnOverlappingError;
  case MDBX_BACKLOG_DEPLETED:
    return BacklogDepletedError;
  case MDBX_DUPLICATED_LCK:
    return DuplicatedLockError;
  case MDBX_DANGLING_DBI:
    return DanglingDbiError;
  case MDBX_OUSTED:
    return OustedError;
  case MDBX_MVCC_RETARDED:
    return MvccRetardedError;
  case MDBX_LAGGARD_READER:
    return LaggardReaderError;
  case MDBX_EROFS:
  case MDBX_EACCESS:
    return ReadonlyError;
  case MDBX_EINVAL:
    return InvalidParameterError;
  case MDBX_EDEADLK:
    return LockError;
  case MDBX_ENOMEM:
    return NoMemoryError;
  case MDBX_EIO:
    return DiskError;
  default:
    return Error;
  }
}

static PyObject *raise_mdbx(int rc, const char *operation) {
  char buffer[256];
  const char *message = mdbx_strerror_r(rc, buffer, sizeof(buffer));
  if (message == NULL)
    message = "unknown libmdbx error";
  PyObject *exception_type = exception_for_code(rc);
  PyObject *text = PyUnicode_FromFormat("%s failed (%d): %s", operation, rc, message);
  PyObject *what = PyUnicode_FromString(operation);
  PyObject *code = PyLong_FromLong(rc);
  PyObject *reason = PyUnicode_FromString(message);
  PyObject *instance = text != NULL ? PyObject_CallOneArg(exception_type, text) : NULL;
  if (text == NULL || what == NULL || code == NULL || reason == NULL || instance == NULL ||
      PyObject_SetAttrString(instance, "what", what) < 0 || PyObject_SetAttrString(instance, "code", code) < 0 ||
      PyObject_SetAttrString(instance, "reason", reason) < 0) {
    Py_XDECREF(text);
    Py_XDECREF(what);
    Py_XDECREF(code);
    Py_XDECREF(reason);
    Py_XDECREF(instance);
    return NULL;
  }
  PyErr_SetObject(exception_type, instance);
  Py_DECREF(text);
  Py_DECREF(what);
  Py_DECREF(code);
  Py_DECREF(reason);
  Py_DECREF(instance);
  return NULL;
}

static Py_ssize_t reap_orphaned_write_txns(EnvObject *env) {
  const unsigned long thread = current_thread();
  Py_ssize_t reaped = 0;
  Py_ssize_t release_env_refs = 0;
  OrphanTxn **link = &env->orphaned_txns;
  while (*link != NULL) {
    OrphanTxn *orphan = *link;
    if (orphan->owner != thread) {
      link = &orphan->next;
      continue;
    }
    int rc = mdbx_txn_abort(orphan->txn);
    if (rc != MDBX_SUCCESS) {
      raise_mdbx(rc, "reaping a write transaction finalized on another thread");
      return -1;
    }
    *link = orphan->next;
    if (env->active_txns > 0)
      env->active_txns--;
    if (env->active_write_txns > 0) {
      env->active_write_txns--;
      if (env->active_write_txns == 0)
        env->write_owner = 0;
    }
    if (orphan->parent != NULL && orphan->parent->active_children > 0)
      orphan->parent->active_children--;
    Py_XDECREF(orphan->parent);
    PyMem_Free(orphan);
    reaped++;
    release_env_refs++;
  }
  /* Each queued native transaction retains its Environment.  Release those
     references only after the list and counters are consistent. */
  while (release_env_refs-- > 0)
    Py_DECREF(env);
  return reaped;
}

static int check_env(EnvObject *self) {
  if (self->closed || self->env == NULL) {
    PyErr_SetString(ClosedError, "environment is closed");
    return 0;
  }
  if (self->pid != current_pid()) {
    PyErr_Format(ForkError,
                 "environment was opened in PID %ld and cannot be reused in forked PID %ld; open a fresh Environment",
                 self->pid, current_pid());
    return 0;
  }
  if (self->orphaned_txns != NULL && reap_orphaned_write_txns(self) < 0)
    return 0;
  return 1;
}

static int begin_env_operation(EnvObject *self) {
  if (!check_env(self))
    return 0;
  self->active_operations++;
  return 1;
}

static void end_env_operation(EnvObject *self) {
  if (self->active_operations > 0)
    self->active_operations--;
}

static int check_txn(TxnObject *self, int require_write) {
  if (self->finished || self->txn == NULL) {
    PyErr_SetString(ClosedError, "transaction is finished");
    return 0;
  }
  if (self->reset) {
    PyErr_SetString(BadTxnError, "read transaction is reset; call renew() before use");
    return 0;
  }
  if (self->broken) {
    PyErr_SetString(BadTxnError, "transaction is broken; abort it before continuing");
    return 0;
  }
  if (!check_env(self->env))
    return 0;
  if (self->owner != current_thread()) {
    PyErr_SetString(ThreadError, "transactions and cursors are bound to the thread that created them");
    return 0;
  }
  if (require_write && self->readonly) {
    PyErr_SetString(BadTxnError, "operation requires a write transaction");
    return 0;
  }
  if (self->active_children) {
    PyErr_SetString(BadTxnError, "parent transaction is blocked while a nested transaction is active");
    return 0;
  }
  return 1;
}

static DbState *find_db_state(EnvObject *env, MDBX_dbi dbi) {
  for (DbState *state = env->db_states; state != NULL; state = state->next) {
    if (state->valid && state->dbi == dbi)
      return state;
  }
  return NULL;
}

static void free_db_state(DbState *state) {
  Py_XDECREF(state->name);
  Py_XDECREF(state->rename_original);
  PyMem_Free(state);
}

static DbState *acquire_db_state(EnvObject *env, MDBX_dbi dbi, TxnObject *creator, PyObject *name) {
  DbState *state = find_db_state(env, dbi);
  if (state == NULL) {
    state = PyMem_Calloc(1, sizeof(*state));
    if (state == NULL) {
      PyErr_NoMemory();
      return NULL;
    }
    state->next = env->db_states;
    state->creator = creator;
    state->name = name == NULL ? Py_NewRef(Py_None) : Py_NewRef(name);
    state->dbi = dbi;
    state->valid = 1;
    env->db_states = state;
  }
  state->wrappers++;
  return state;
}

static void remove_unused_db_state(EnvObject *env, DbState *state) {
  if (state == NULL || state->valid || state->wrappers != 0)
    return;
  DbState **link = &env->db_states;
  while (*link != NULL) {
    if (*link == state) {
      *link = state->next;
      free_db_state(state);
      return;
    }
    link = &(*link)->next;
  }
}

static void release_db_state(EnvObject *env, DbState **slot) {
  DbState *state = *slot;
  if (state == NULL)
    return;
  *slot = NULL;
  if (state->wrappers > 0)
    state->wrappers--;
  remove_unused_db_state(env, state);
}

static void invalidate_db_state(EnvObject *env, DbState *state) {
  if (state == NULL)
    return;
  state->valid = 0;
  state->creator = NULL;
  state->rename_txn = NULL;
  Py_CLEAR(state->rename_original);
  remove_unused_db_state(env, state);
}

static void resolve_db_state_changes(TxnObject *txn, int committed) {
  if (!txn->has_db_changes)
    return;
  DbState **link = &txn->env->db_states;
  while (*link != NULL) {
    DbState *state = *link;
    if (state->creator == txn) {
      if (committed) {
        /* A create committed into a nested parent remains provisional until
           the outermost write transaction commits. */
        state->creator = txn->parent;
      } else {
        state->creator = NULL;
        state->valid = 0;
      }
    }
    if (state->rename_txn == txn) {
      if (committed) {
        state->rename_txn = txn->parent;
        if (txn->parent == NULL)
          Py_CLEAR(state->rename_original);
      } else {
        state->rename_txn = NULL;
        if (state->rename_original != NULL)
          Py_SETREF(state->name, state->rename_original);
        state->rename_original = NULL;
      }
    }
    if (state->wrappers == 0) {
      if (!state->valid) {
        *link = state->next;
        free_db_state(state);
        continue;
      }
      link = &state->next;
    } else {
      link = &state->next;
    }
  }
  if (committed && txn->parent != NULL)
    txn->parent->has_db_changes = 1;
  txn->has_db_changes = 0;
}

static int check_db(DbObject *db, TxnObject *txn) {
  if (db == NULL)
    return 1;
  if (db->closed || db->state == NULL || !db->state->valid) {
    PyErr_SetString(ClosedError, "database handle is closed");
    return 0;
  }
  if (db->env != txn->env) {
    PyErr_SetString(PyExc_ValueError, "database handle belongs to a different environment");
    return 0;
  }
  if (db->state->creator != NULL) {
    TxnObject *scope = txn;
    while (scope != NULL && scope != db->state->creator)
      scope = scope->parent;
    if (scope == NULL) {
      PyErr_SetString(BadTxnError, "database was created by another uncommitted transaction");
      return 0;
    }
  }
  return check_env(db->env);
}

static int check_cursor(CursorObject *self, int require_write) {
  if (self->closed || self->cursor == NULL) {
    PyErr_SetString(ClosedError, "cursor is closed");
    return 0;
  }
  if (self->owner != current_thread()) {
    PyErr_SetString(ThreadError, "transactions and cursors are bound to the thread that created them");
    return 0;
  }
  return check_txn(self->txn, require_write) && check_db(self->db, self->txn);
}

static int object_to_val(PyObject *obj, Py_buffer *view, MDBX_val *val, const char *what) {
  if (PyObject_GetBuffer(obj, view, PyBUF_SIMPLE) < 0) {
    PyErr_Format(PyExc_TypeError, "%s must be a contiguous bytes-like object", what);
    return 0;
  }
  if (view->len < 0) {
    PyBuffer_Release(view);
    PyErr_Format(PyExc_ValueError, "%s has an invalid negative length", what);
    return 0;
  }
  val->iov_base = view->buf;
  val->iov_len = (size_t)view->len;
  return 1;
}

static PyObject *val_to_bytes(const MDBX_val *val) {
  if (val->iov_len > (size_t)PY_SSIZE_T_MAX) {
    PyErr_SetString(PyExc_OverflowError, "libmdbx value is too large for a Python bytes object");
    return NULL;
  }
  return PyBytes_FromStringAndSize((const char *)val->iov_base, (Py_ssize_t)val->iov_len);
}

static int dict_set_u64(PyObject *dict, const char *key, uint64_t value) {
  PyObject *obj = PyLong_FromUnsignedLongLong(value);
  if (obj == NULL)
    return -1;
  int rc = PyDict_SetItemString(dict, key, obj);
  Py_DECREF(obj);
  return rc;
}

static int dict_set_str(PyObject *dict, const char *key, const char *value) {
  PyObject *obj = value ? PyUnicode_FromString(value) : Py_NewRef(Py_None);
  if (obj == NULL)
    return -1;
  int rc = PyDict_SetItemString(dict, key, obj);
  Py_DECREF(obj);
  return rc;
}

static PyObject *stat_to_dict(const MDBX_stat *stat) {
  PyObject *dict = PyDict_New();
  if (dict == NULL)
    return NULL;
  if (dict_set_u64(dict, "page_size", stat->ms_psize) < 0 ||
      dict_set_u64(dict, "depth", stat->ms_depth) < 0 ||
      dict_set_u64(dict, "branch_pages", stat->ms_branch_pages) < 0 ||
      dict_set_u64(dict, "leaf_pages", stat->ms_leaf_pages) < 0 ||
      dict_set_u64(dict, "overflow_pages", stat->ms_overflow_pages) < 0 ||
      dict_set_u64(dict, "entries", stat->ms_entries) < 0 ||
      dict_set_u64(dict, "mod_txnid", stat->ms_mod_txnid) < 0) {
    Py_DECREF(dict);
    return NULL;
  }
  return dict;
}

static int parse_geometry(PyObject *obj, intptr_t values[6]) {
  PyObject *seq = PySequence_Fast(obj, "geometry must be a six-item sequence: lower, now, upper, grow, shrink, pagesize");
  if (seq == NULL)
    return 0;
  if (PySequence_Fast_GET_SIZE(seq) != 6) {
    Py_DECREF(seq);
    PyErr_SetString(PyExc_ValueError, "geometry must contain exactly six integers");
    return 0;
  }
  for (Py_ssize_t i = 0; i < 6; ++i) {
    PyObject *item = PySequence_Fast_GET_ITEM(seq, i);
    long long n = PyLong_AsLongLong(item);
    if (n == -1 && PyErr_Occurred()) {
      Py_DECREF(seq);
      return 0;
    }
    values[i] = (intptr_t)n;
    if ((long long)values[i] != n) {
      Py_DECREF(seq);
      PyErr_SetString(PyExc_OverflowError, "geometry value does not fit intptr_t");
      return 0;
    }
  }
  Py_DECREF(seq);
  return 1;
}

static int parse_uint32(PyObject *obj, uint32_t *value, const char *name) {
  unsigned long long parsed = PyLong_AsUnsignedLongLong(obj);
  if (PyErr_Occurred()) {
    if (PyErr_ExceptionMatches(PyExc_OverflowError)) {
      PyErr_Clear();
      PyErr_Format(PyExc_OverflowError, "%s must fit uint32", name);
    }
    return 0;
  }
  if (parsed > UINT32_MAX) {
    PyErr_Format(PyExc_OverflowError, "%s must fit uint32", name);
    return 0;
  }
  *value = (uint32_t)parsed;
  return 1;
}

static int parse_uint64(PyObject *obj, uint64_t *value, const char *name) {
  unsigned long long parsed = PyLong_AsUnsignedLongLong(obj);
  if (PyErr_Occurred()) {
    if (PyErr_ExceptionMatches(PyExc_OverflowError)) {
      PyErr_Clear();
      PyErr_Format(PyExc_OverflowError, "%s must fit uint64", name);
    }
    return 0;
  }
  *value = (uint64_t)parsed;
  return 1;
}

static int apply_options(MDBX_env *env, PyObject *options) {
  if (options == NULL || options == Py_None)
    return 1;
  if (!PyMapping_Check(options)) {
    PyErr_SetString(PyExc_TypeError, "options must be a mapping of integer option IDs to integer values");
    return 0;
  }
  PyObject *items = PyMapping_Items(options);
  if (items == NULL)
    return 0;
  PyObject *seq = PySequence_Fast(items, "options.items() must return a sequence");
  Py_DECREF(items);
  if (seq == NULL)
    return 0;
  Py_ssize_t size = PySequence_Fast_GET_SIZE(seq);
  for (Py_ssize_t i = 0; i < size; ++i) {
    PyObject *pair = PySequence_Fast_GET_ITEM(seq, i);
    if (!PyTuple_Check(pair) || PyTuple_GET_SIZE(pair) != 2) {
      Py_DECREF(seq);
      PyErr_SetString(PyExc_TypeError, "options items must be key/value pairs");
      return 0;
    }
    long option = PyLong_AsLong(PyTuple_GET_ITEM(pair, 0));
    unsigned long long value = PyLong_AsUnsignedLongLong(PyTuple_GET_ITEM(pair, 1));
    if (PyErr_Occurred()) {
      Py_DECREF(seq);
      return 0;
    }
    int rc = mdbx_env_set_option(env, (MDBX_option_t)option, (uint64_t)value);
    if (rc != MDBX_SUCCESS) {
      Py_DECREF(seq);
      raise_mdbx(rc, "mdbx_env_set_option");
      return 0;
    }
  }
  Py_DECREF(seq);
  return 1;
}

static int reject_embedded_nul(PyObject *fspath) {
  if (PyBytes_Check(fspath)) {
    if (memchr(PyBytes_AS_STRING(fspath), '\0', (size_t)PyBytes_GET_SIZE(fspath)) != NULL) {
      PyErr_SetString(PyExc_ValueError, "filesystem path contains an embedded NUL byte");
      return 0;
    }
    return 1;
  }
  if (PyUnicode_Check(fspath)) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(fspath);
    for (Py_ssize_t i = 0; i < length; ++i) {
      Py_UCS4 character = PyUnicode_ReadChar(fspath, i);
      if (character == (Py_UCS4)-1 && PyErr_Occurred())
        return 0;
      if (character == 0) {
        PyErr_SetString(PyExc_ValueError, "filesystem path contains an embedded NUL character");
        return 0;
      }
    }
    return 1;
  }
  PyErr_SetString(PyExc_TypeError, "__fspath__() must return str or bytes");
  return 0;
}

static int open_native_path(MDBX_env *env, PyObject *path, MDBX_env_flags_t flags, mdbx_mode_t mode) {
  PyObject *fspath = PyOS_FSPath(path);
  if (fspath == NULL)
    return -1;
  if (!reject_embedded_nul(fspath)) {
    Py_DECREF(fspath);
    return -1;
  }
#ifdef _WIN32
  PyObject *unicode = fspath;
  if (PyBytes_Check(fspath)) {
    unicode = PyUnicode_DecodeFSDefaultAndSize(PyBytes_AS_STRING(fspath), PyBytes_GET_SIZE(fspath));
    Py_DECREF(fspath);
    if (unicode == NULL)
      return -1;
  }
  wchar_t *wide = PyUnicode_AsWideCharString(unicode, NULL);
  Py_DECREF(unicode);
  if (wide == NULL)
    return -1;
  int rc;
  Py_BEGIN_ALLOW_THREADS
  rc = mdbx_env_openW(env, wide, flags, mode);
  Py_END_ALLOW_THREADS
  PyMem_Free(wide);
#else
  PyObject *bytes = fspath;
  if (PyUnicode_Check(fspath)) {
    bytes = PyUnicode_EncodeFSDefault(fspath);
    Py_DECREF(fspath);
    if (bytes == NULL)
      return -1;
  }
  int rc;
  Py_BEGIN_ALLOW_THREADS
  rc = mdbx_env_open(env, PyBytes_AS_STRING(bytes), flags, mode);
  Py_END_ALLOW_THREADS
  Py_DECREF(bytes);
#endif
  return rc;
}

static PyObject *Env_new(PyTypeObject *type, PyObject *args, PyObject *kwargs) {
  (void)args;
  (void)kwargs;
  EnvObject *self = (EnvObject *)type->tp_alloc(type, 0);
  if (self != NULL) {
    self->env = NULL;
    self->path = NULL;
    self->db_states = NULL;
    self->orphaned_txns = NULL;
    self->main_dbi = 0;
    self->pid = current_pid();
    self->active_txns = 0;
    self->active_write_txns = 0;
    self->active_operations = 0;
    self->write_owner = 0;
    self->closed = 1;
  }
  return (PyObject *)self;
}

static int Env_init(EnvObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"path", "flags", "mode", "max_readers", "max_dbs", "geometry", "options", "readonly",
                           "subdir", NULL};
  PyObject *path;
  PyObject *flags_obj = NULL, *mode_obj = NULL, *max_readers_obj = NULL, *max_dbs_obj = NULL;
  uint32_t flags = 0;
  uint32_t mode = 0664;
  uint32_t max_readers = 126;
  uint32_t max_dbs = 64;
  PyObject *geometry = Py_None;
  PyObject *options = Py_None;
  int readonly = 0;
  int subdir = 1;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OOOOOOpp:Environment", kwlist, &path, &flags_obj, &mode_obj,
                                   &max_readers_obj, &max_dbs_obj, &geometry, &options, &readonly, &subdir))
    return -1;
  if ((flags_obj != NULL && !parse_uint32(flags_obj, &flags, "flags")) ||
      (mode_obj != NULL && !parse_uint32(mode_obj, &mode, "mode")) ||
      (max_readers_obj != NULL && !parse_uint32(max_readers_obj, &max_readers, "max_readers")) ||
      (max_dbs_obj != NULL && !parse_uint32(max_dbs_obj, &max_dbs, "max_dbs")))
    return -1;
  /* path is retained for the lifetime of the wrapper, including after close,
     and therefore also acts as an immutable "initialized once" marker.  A
     second __init__ after close would otherwise leak the first path and reuse
     stale environment-owned DBI bookkeeping. */
  if (self->path != NULL) {
    PyErr_SetString(PyExc_RuntimeError, "Environment.__init__ called more than once");
    return -1;
  }
  MDBX_env *env = NULL;
  int rc = mdbx_env_create(&env);
  if (rc != MDBX_SUCCESS) {
    raise_mdbx(rc, "mdbx_env_create");
    return -1;
  }
  rc = mdbx_env_set_maxreaders(env, (unsigned)max_readers);
  if (rc == MDBX_SUCCESS)
    rc = mdbx_env_set_maxdbs(env, (MDBX_dbi)max_dbs);
  if (rc == MDBX_SUCCESS && geometry != Py_None) {
    intptr_t geo[6];
    if (!parse_geometry(geometry, geo)) {
      mdbx_env_close_ex(env, true);
      return -1;
    }
    rc = mdbx_env_set_geometry(env, geo[0], geo[1], geo[2], geo[3], geo[4], geo[5]);
  }
  if (rc != MDBX_SUCCESS) {
    mdbx_env_close_ex(env, true);
    raise_mdbx(rc, "environment configuration");
    return -1;
  }
  if (!apply_options(env, options)) {
    mdbx_env_close_ex(env, true);
    return -1;
  }
  if (readonly)
    flags |= MDBX_RDONLY;
  if (!subdir)
    flags |= MDBX_NOSUBDIR;
  /* Native handles may be finalized by a different Python thread when the
     last reference moves between worker queues.  MDBX_NOSTICKYTHREADS makes
     cleanup safe; the wrapper still enforces a stricter owner-thread policy
     for every public Transaction and Cursor operation. */
  flags |= MDBX_NOSTICKYTHREADS;
  rc = open_native_path(env, path, (MDBX_env_flags_t)flags, (mdbx_mode_t)mode);
  if (rc != MDBX_SUCCESS) {
    mdbx_env_close_ex(env, true);
    if (PyErr_Occurred())
      return -1;
    raise_mdbx(rc, "mdbx_env_open");
    return -1;
  }
  MDBX_txn *probe = NULL;
  rc = mdbx_txn_begin(env, NULL, MDBX_TXN_RDONLY, &probe);
  if (rc == MDBX_SUCCESS)
    rc = mdbx_dbi_open(probe, NULL, MDBX_DB_DEFAULTS, &self->main_dbi);
  if (probe != NULL)
    (void)mdbx_txn_abort(probe);
  if (rc != MDBX_SUCCESS) {
    mdbx_env_close_ex(env, true);
    raise_mdbx(rc, "opening the default database handle");
    return -1;
  }
  self->env = env;
  self->path = Py_NewRef(path);
  self->pid = current_pid();
  self->closed = 0;
  return 0;
}

static void Env_dealloc(EnvObject *self) {
  if (self->env != NULL && self->pid == current_pid())
    (void)mdbx_env_close_ex(self->env, false);
  self->env = NULL;
  while (self->db_states != NULL) {
    DbState *state = self->db_states;
    self->db_states = state->next;
    free_db_state(state);
  }
  Py_XDECREF(self->path);
  Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *Env_close(EnvObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"dont_sync", NULL};
  int dont_sync = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|p:close", kwlist, &dont_sync))
    return NULL;
  if (self->closed || self->env == NULL)
    Py_RETURN_NONE;
  if (self->pid != current_pid()) {
    self->env = NULL;
    self->closed = 1;
    Py_RETURN_NONE;
  }
  if (self->orphaned_txns != NULL && reap_orphaned_write_txns(self) < 0)
    return NULL;
  if (self->active_txns != 0) {
    PyErr_Format(BusyError, "cannot close environment while %zd transaction(s) are active", self->active_txns);
    return NULL;
  }
  if (self->active_operations != 0) {
    PyErr_Format(BusyError, "cannot close environment while %zd native operation(s) are active",
                 self->active_operations);
    return NULL;
  }
  MDBX_env *env = self->env;
  self->env = NULL;
  self->closed = 1;
  int rc;
  Py_BEGIN_ALLOW_THREADS
  rc = mdbx_env_close_ex(env, dont_sync != 0);
  Py_END_ALLOW_THREADS
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_env_close_ex");
  Py_RETURN_NONE;
}

static PyObject *Env_reap_orphaned_transactions(EnvObject *self, PyObject *Py_UNUSED(ignored)) {
  if (self->closed || self->env == NULL) {
    PyErr_SetString(ClosedError, "environment is closed");
    return NULL;
  }
  if (self->pid != current_pid()) {
    PyErr_Format(ForkError,
                 "environment was opened in PID %ld and cannot be reused in forked PID %ld; open a fresh Environment",
                 self->pid, current_pid());
    return NULL;
  }
  Py_ssize_t reaped = reap_orphaned_write_txns(self);
  return reaped < 0 ? NULL : PyLong_FromSsize_t(reaped);
}

static PyObject *Env_enter(EnvObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_env(self))
    return NULL;
  return Py_NewRef(self);
}

static PyObject *Env_exit(EnvObject *self, PyObject *args) {
  (void)args;
  PyObject *empty = PyTuple_New(0);
  if (empty == NULL)
    return NULL;
  PyObject *result = Env_close(self, empty, NULL);
  Py_DECREF(empty);
  return result;
}

static PyObject *Env_stat(EnvObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!begin_env_operation(self))
    return NULL;
  MDBX_stat stat;
  int rc;
  if (self->active_write_txns != 0 && self->write_owner == current_thread()) {
    /* Passing txn=NULL makes libmdbx use the current thread's writer when it
       owns one.  Retain the GIL in that case so cursor finalizers cannot race
       the writer; otherwise release it because libmdbx may wait for another
       thread's writer lock. */
    rc = mdbx_env_stat_ex(self->env, NULL, &stat, sizeof(stat));
  } else {
    Py_BEGIN_ALLOW_THREADS
    rc = mdbx_env_stat_ex(self->env, NULL, &stat, sizeof(stat));
    Py_END_ALLOW_THREADS
  }
  end_env_operation(self);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_env_stat_ex");
  return stat_to_dict(&stat);
}

static PyObject *Env_info(EnvObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_env(self))
    return NULL;
  MDBX_envinfo info;
  int rc = mdbx_env_info_ex(self->env, NULL, &info, sizeof(info));
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_env_info_ex");
  PyObject *dict = PyDict_New();
  PyObject *geo = PyDict_New();
  PyObject *pgop = PyDict_New();
  if (dict == NULL || geo == NULL || pgop == NULL)
    goto error;
  if (dict_set_u64(geo, "lower", info.mi_geo.lower) < 0 || dict_set_u64(geo, "upper", info.mi_geo.upper) < 0 ||
      dict_set_u64(geo, "current", info.mi_geo.current) < 0 || dict_set_u64(geo, "shrink", info.mi_geo.shrink) < 0 ||
      dict_set_u64(geo, "grow", info.mi_geo.grow) < 0 || PyDict_SetItemString(dict, "geometry", geo) < 0 ||
      dict_set_u64(dict, "map_size", info.mi_mapsize) < 0 ||
      dict_set_u64(dict, "file_size", info.mi_dxb_fsize) < 0 ||
      dict_set_u64(dict, "file_allocated", info.mi_dxb_fallocated) < 0 ||
      dict_set_u64(dict, "last_pgno", info.mi_last_pgno) < 0 ||
      dict_set_u64(dict, "recent_txnid", info.mi_recent_txnid) < 0 ||
      dict_set_u64(dict, "latter_reader_txnid", info.mi_latter_reader_txnid) < 0 ||
      dict_set_u64(dict, "self_latter_reader_txnid", info.mi_self_latter_reader_txnid) < 0 ||
      dict_set_u64(dict, "max_readers", info.mi_maxreaders) < 0 ||
      dict_set_u64(dict, "num_readers", info.mi_numreaders) < 0 ||
      dict_set_u64(dict, "page_size", info.mi_dxb_pagesize) < 0 ||
      dict_set_u64(dict, "system_page_size", info.mi_sys_pagesize) < 0 ||
      dict_set_u64(dict, "unsynced_bytes", info.mi_unsync_volume) < 0 ||
      dict_set_u64(dict, "autosync_threshold", info.mi_autosync_threshold) < 0 ||
      dict_set_u64(dict, "mode", info.mi_mode) < 0 || dict_set_u64(pgop, "newly", info.mi_pgop_stat.newly) < 0 ||
      dict_set_u64(pgop, "cow", info.mi_pgop_stat.cow) < 0 ||
      dict_set_u64(pgop, "clone", info.mi_pgop_stat.clone) < 0 ||
      dict_set_u64(pgop, "split", info.mi_pgop_stat.split) < 0 ||
      dict_set_u64(pgop, "merge", info.mi_pgop_stat.merge) < 0 ||
      dict_set_u64(pgop, "spill", info.mi_pgop_stat.spill) < 0 ||
      dict_set_u64(pgop, "unspill", info.mi_pgop_stat.unspill) < 0 ||
      dict_set_u64(pgop, "write_ops", info.mi_pgop_stat.wops) < 0 ||
      dict_set_u64(pgop, "msync", info.mi_pgop_stat.msync) < 0 ||
      dict_set_u64(pgop, "fsync", info.mi_pgop_stat.fsync) < 0 ||
      PyDict_SetItemString(dict, "page_ops", pgop) < 0)
    goto error;
  Py_DECREF(geo);
  Py_DECREF(pgop);
  return dict;
error:
  Py_XDECREF(dict);
  Py_XDECREF(geo);
  Py_XDECREF(pgop);
  return NULL;
}

static PyObject *Env_sync(EnvObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"force", "nonblock", NULL};
  int force = 1, nonblock = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|pp:sync", kwlist, &force, &nonblock))
    return NULL;
  if (!begin_env_operation(self))
    return NULL;
  int rc;
  Py_BEGIN_ALLOW_THREADS
  rc = mdbx_env_sync_ex(self->env, force != 0, nonblock != 0);
  Py_END_ALLOW_THREADS
  end_env_operation(self);
  if (rc != MDBX_SUCCESS && rc != MDBX_RESULT_TRUE)
    return raise_mdbx(rc, "mdbx_env_sync_ex");
  return PyBool_FromLong(rc == MDBX_RESULT_TRUE);
}

static int native_copy_path(MDBX_env *env, PyObject *path, MDBX_copy_flags_t flags) {
  PyObject *fspath = PyOS_FSPath(path);
  if (fspath == NULL)
    return -1;
  if (!reject_embedded_nul(fspath)) {
    Py_DECREF(fspath);
    return -1;
  }
#ifdef _WIN32
  PyObject *unicode = fspath;
  if (PyBytes_Check(fspath)) {
    unicode = PyUnicode_DecodeFSDefaultAndSize(PyBytes_AS_STRING(fspath), PyBytes_GET_SIZE(fspath));
    Py_DECREF(fspath);
    if (unicode == NULL)
      return -1;
  }
  wchar_t *wide = PyUnicode_AsWideCharString(unicode, NULL);
  Py_DECREF(unicode);
  if (wide == NULL)
    return -1;
  int rc;
  Py_BEGIN_ALLOW_THREADS
  rc = mdbx_env_copyW(env, wide, flags);
  Py_END_ALLOW_THREADS
  PyMem_Free(wide);
#else
  PyObject *bytes = fspath;
  if (PyUnicode_Check(fspath)) {
    bytes = PyUnicode_EncodeFSDefault(fspath);
    Py_DECREF(fspath);
    if (bytes == NULL)
      return -1;
  }
  int rc;
  Py_BEGIN_ALLOW_THREADS
  rc = mdbx_env_copy(env, PyBytes_AS_STRING(bytes), flags);
  Py_END_ALLOW_THREADS
  Py_DECREF(bytes);
#endif
  return rc;
}

static PyObject *Env_copy(EnvObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"path", "flags", NULL};
  PyObject *path, *flags_obj = NULL;
  uint32_t flags = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|O:copy", kwlist, &path, &flags_obj))
    return NULL;
  if (flags_obj != NULL && !parse_uint32(flags_obj, &flags, "copy flags"))
    return NULL;
  if (!begin_env_operation(self))
    return NULL;
  int rc = native_copy_path(self->env, path, (MDBX_copy_flags_t)flags);
  end_env_operation(self);
  if (rc == -1 && PyErr_Occurred())
    return NULL;
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_env_copy");
  Py_RETURN_NONE;
}

static PyObject *Env_set_geometry(EnvObject *self, PyObject *args) {
  PyObject *geometry;
  if (!PyArg_ParseTuple(args, "O:set_geometry", &geometry))
    return NULL;
  intptr_t geo[6];
  if (!parse_geometry(geometry, geo))
    return NULL;
  if (!begin_env_operation(self))
    return NULL;
  int rc;
  Py_BEGIN_ALLOW_THREADS
  rc = mdbx_env_set_geometry(self->env, geo[0], geo[1], geo[2], geo[3], geo[4], geo[5]);
  Py_END_ALLOW_THREADS
  end_env_operation(self);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_env_set_geometry");
  Py_RETURN_NONE;
}

static PyObject *Env_get_flags(EnvObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_env(self))
    return NULL;
  unsigned flags;
  int rc = mdbx_env_get_flags(self->env, &flags);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_env_get_flags");
  return PyLong_FromUnsignedLong(flags);
}

static PyObject *Env_set_flags(EnvObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"flags", "enabled", NULL};
  PyObject *flags_obj;
  uint32_t flags;
  int enabled = 1;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|p:set_flags", kwlist, &flags_obj, &enabled))
    return NULL;
  if (!parse_uint32(flags_obj, &flags, "flags"))
    return NULL;
  if (!begin_env_operation(self))
    return NULL;
  int rc;
  if (self->active_write_txns != 0 && self->write_owner == current_thread()) {
    /* Upstream deliberately lets a writer's owner change these settings
       without reacquiring its own lock.  Keep the GIL so a cursor finalizer
       cannot concurrently mutate that transaction's cursor list. */
    rc = mdbx_env_set_flags(self->env, (MDBX_env_flags_t)flags, enabled != 0);
  } else {
    Py_BEGIN_ALLOW_THREADS
    rc = mdbx_env_set_flags(self->env, (MDBX_env_flags_t)flags, enabled != 0);
    Py_END_ALLOW_THREADS
  }
  end_env_operation(self);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_env_set_flags");
  Py_RETURN_NONE;
}

static PyObject *Env_get_option(EnvObject *self, PyObject *arg) {
  if (!check_env(self))
    return NULL;
  long option = PyLong_AsLong(arg);
  if (option == -1 && PyErr_Occurred())
    return NULL;
  uint64_t value;
  int rc = mdbx_env_get_option(self->env, (MDBX_option_t)option, &value);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_env_get_option");
  return PyLong_FromUnsignedLongLong(value);
}

static PyObject *Env_set_option(EnvObject *self, PyObject *args) {
  long option;
  PyObject *value_obj;
  uint64_t value;
  if (!PyArg_ParseTuple(args, "lO:set_option", &option, &value_obj))
    return NULL;
  if (!parse_uint64(value_obj, &value, "option value"))
    return NULL;
  if (!begin_env_operation(self))
    return NULL;
  int rc;
  if (self->active_write_txns != 0 && self->write_owner == current_thread()) {
    rc = mdbx_env_set_option(self->env, (MDBX_option_t)option, (uint64_t)value);
  } else {
    Py_BEGIN_ALLOW_THREADS
    rc = mdbx_env_set_option(self->env, (MDBX_option_t)option, (uint64_t)value);
    Py_END_ALLOW_THREADS
  }
  end_env_operation(self);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_env_set_option");
  Py_RETURN_NONE;
}

static PyObject *Env_reader_check(EnvObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_env(self))
    return NULL;
  int dead = 0;
  int rc = mdbx_reader_check(self->env, &dead);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_reader_check");
  return PyLong_FromLong(dead);
}

static PyObject *Env_defrag(EnvObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"at_least_pages", "at_least_time", "enough_pages", "time_limit", "acceptable_backlash",
                           "preferred_batch", NULL};
  Py_ssize_t at_least_pages = 0, at_least_time = 0, enough_pages = 0, time_limit = 0;
  Py_ssize_t acceptable_backlash = -1, preferred_batch = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|nnnnnn:defrag", kwlist, &at_least_pages, &at_least_time,
                                   &enough_pages, &time_limit, &acceptable_backlash, &preferred_batch))
    return NULL;
  if (at_least_pages < 0 || at_least_time < 0 || enough_pages < 0 || time_limit < 0 || preferred_batch < 0) {
    PyErr_SetString(PyExc_ValueError, "defragmentation counts and time limits must be non-negative");
    return NULL;
  }
  if (!begin_env_operation(self))
    return NULL;
  MDBX_defrag_result_t result;
  memset(&result, 0, sizeof(result));
  int rc;
  Py_BEGIN_ALLOW_THREADS
  rc = mdbx_env_defrag(self->env, (size_t)at_least_pages, (size_t)at_least_time, (size_t)enough_pages,
                       (size_t)time_limit,
                       (intptr_t)acceptable_backlash, (intptr_t)preferred_batch, NULL, NULL, &result);
  Py_END_ALLOW_THREADS
  end_env_operation(self);
  if (rc != MDBX_SUCCESS && rc != MDBX_RESULT_TRUE)
    return raise_mdbx(rc, "mdbx_env_defrag");
  PyObject *dict = PyDict_New();
  if (dict == NULL)
    return NULL;
  PyObject *shrunk = PyLong_FromLongLong((long long)result.pages_shrinked);
  if (shrunk == NULL || PyDict_SetItemString(dict, "pages_shrunk", shrunk) < 0 ||
      dict_set_u64(dict, "pages_moved", result.pages_moved) < 0 ||
      dict_set_u64(dict, "pages_scheduled", result.pages_scheduled) < 0 ||
      dict_set_u64(dict, "pages_retained", result.pages_retained) < 0 ||
      dict_set_u64(dict, "pages_left", result.pages_left) < 0 ||
      dict_set_u64(dict, "pages_whole", result.pages_whole) < 0 ||
      dict_set_u64(dict, "obstructed_pgno", result.obstructed_pgno) < 0 ||
      dict_set_u64(dict, "obstructed_span", result.obstructed_span) < 0 ||
      dict_set_u64(dict, "obstructed_txnid", result.obstructed_txnid) < 0 ||
      dict_set_u64(dict, "obstructor_pid", (uint64_t)result.obstructor_pid) < 0 ||
      dict_set_u64(dict, "cycle_progress_permille", result.rough_estimation_cycle_progress_permille) < 0 ||
      dict_set_u64(dict, "cycles", result.cycles) < 0 ||
      dict_set_u64(dict, "stopping_reasons", result.stopping_reasons) < 0 ||
      dict_set_u64(dict, "spent_time_dot16", result.spent_time_dot16) < 0) {
    Py_XDECREF(shrunk);
    Py_DECREF(dict);
    return NULL;
  }
  Py_DECREF(shrunk);
  PyObject *complete = PyBool_FromLong(rc == MDBX_SUCCESS);
  if (complete == NULL || PyDict_SetItemString(dict, "complete", complete) < 0) {
    Py_XDECREF(complete);
    Py_DECREF(dict);
    return NULL;
  }
  Py_DECREF(complete);
  return dict;
}

static PyObject *Env_warmup(EnvObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"flags", "timeout", NULL};
  PyObject *flags_obj = NULL, *timeout_obj = NULL;
  uint32_t flags = 0, timeout = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OO:warmup", kwlist, &flags_obj, &timeout_obj))
    return NULL;
  if ((flags_obj != NULL && !parse_uint32(flags_obj, &flags, "warmup flags")) ||
      (timeout_obj != NULL && !parse_uint32(timeout_obj, &timeout, "warmup timeout")))
    return NULL;
  if (!begin_env_operation(self))
    return NULL;
  int rc;
  Py_BEGIN_ALLOW_THREADS
  rc = mdbx_env_warmup(self->env, NULL, (MDBX_warmup_flags_t)flags, timeout);
  Py_END_ALLOW_THREADS
  end_env_operation(self);
  if (rc != MDBX_SUCCESS && rc != MDBX_RESULT_TRUE)
    return raise_mdbx(rc, "mdbx_env_warmup");
  return PyBool_FromLong(rc == MDBX_SUCCESS);
}

static PyObject *Env_get_closed(EnvObject *self, void *closure) {
  (void)closure;
  return PyBool_FromLong(self->closed || self->env == NULL);
}

static PyObject *Env_get_path(EnvObject *self, void *closure) {
  (void)closure;
  if (self->path == NULL)
    Py_RETURN_NONE;
  return Py_NewRef(self->path);
}

static void txn_finish_bookkeeping(TxnObject *self) {
  if (!self->finished) {
    self->finished = 1;
    if (self->env != NULL && self->env->active_txns > 0)
      self->env->active_txns--;
    if (!self->readonly && self->env != NULL && self->env->active_write_txns > 0) {
      self->env->active_write_txns--;
      if (self->env->active_write_txns == 0)
        self->env->write_owner = 0;
    }
    if (self->parent != NULL && self->parent->active_children > 0)
      self->parent->active_children--;
  }
  self->reset = 0;
}

/* Closing or rebinding a cursor touches the transaction's internal cursor
   list.  libmdbx explicitly forbids doing that concurrently with any use of
   the same transaction.  The GIL ordinarily serializes those operations, but
   selected long-running paths intentionally release it. */
static int txn_chain_has_open_cursors(const TxnObject *txn) {
  for (const TxnObject *scope = txn; scope != NULL; scope = scope->parent) {
    if (scope->open_cursors != 0)
      return 1;
  }
  return 0;
}

static PyObject *new_transaction(EnvObject *env, TxnObject *parent, MDBX_txn *native, int readonly) {
  TxnObject *self = (TxnObject *)TxnType.tp_alloc(&TxnType, 0);
  if (self == NULL)
    return NULL;
  self->txn = native;
  self->env = (EnvObject *)Py_NewRef(env);
  self->parent = parent ? (TxnObject *)Py_NewRef(parent) : NULL;
  self->owner = current_thread();
  self->active_children = 0;
  self->open_cursors = 0;
  self->has_db_changes = 0;
  self->readonly = readonly;
  self->reset = 0;
  self->broken = 0;
  self->finished = 0;
  env->active_txns++;
  if (!readonly) {
    if (env->active_write_txns == 0)
      env->write_owner = self->owner;
    env->active_write_txns++;
  }
  if (parent != NULL)
    parent->active_children++;
  return (PyObject *)self;
}

static PyObject *Env_begin(EnvObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"write", "parent", "flags", NULL};
  int write = 0;
  PyObject *parent_obj = Py_None;
  PyObject *flags_obj = NULL;
  uint32_t flags = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|pOO:begin", kwlist, &write, &parent_obj, &flags_obj))
    return NULL;
  if (flags_obj != NULL && !parse_uint32(flags_obj, &flags, "transaction flags"))
    return NULL;
  if (write && (flags & MDBX_TXN_RDONLY)) {
    PyErr_SetString(PyExc_ValueError, "write=True conflicts with MDBX_TXN_RDONLY flags");
    return NULL;
  }
  if (!check_env(self))
    return NULL;
  TxnObject *parent = NULL;
  MDBX_txn *parent_native = NULL;
  if (parent_obj != Py_None) {
    if (!PyObject_TypeCheck(parent_obj, &TxnType)) {
      PyErr_SetString(PyExc_TypeError, "parent must be a Transaction or None");
      return NULL;
    }
    parent = (TxnObject *)parent_obj;
    if (parent->env != self) {
      PyErr_SetString(PyExc_ValueError, "parent transaction belongs to a different environment");
      return NULL;
    }
    if (!check_txn(parent, write))
      return NULL;
    if (parent->active_children) {
      PyErr_SetString(BadTxnError, "parent transaction already has an active nested transaction");
      return NULL;
    }
    parent_native = parent->txn;
  }
  MDBX_txn_flags_t txn_flags = (MDBX_txn_flags_t)(flags | (write ? 0u : (unsigned)MDBX_TXN_RDONLY));
  MDBX_txn *txn = NULL;
  int rc;
  self->active_operations++;
  if (parent != NULL && txn_chain_has_open_cursors(parent)) {
    /* A nested begin manipulates the parent's cursor backup chain. */
    rc = mdbx_txn_begin(self->env, parent_native, txn_flags, &txn);
  } else {
    Py_BEGIN_ALLOW_THREADS
    rc = mdbx_txn_begin(self->env, parent_native, txn_flags, &txn);
    Py_END_ALLOW_THREADS
  }
  end_env_operation(self);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_txn_begin");
  PyObject *result = new_transaction(self, parent, txn, !write);
  if (result == NULL) {
    (void)mdbx_txn_abort(txn);
  } else if (!write && (flags & MDBX_NOMEMINIT)) {
    /* MDBX_TXN_RDONLY_PREPARE is MDBX_RDONLY | MDBX_NOMEMINIT.  The
       preallocated handle has the same public state as reset() and becomes
       usable through renew(). */
    ((TxnObject *)result)->reset = 1;
  }
  return result;
}

static PyObject *Env_read(EnvObject *self, PyObject *Py_UNUSED(ignored)) {
  PyObject *args = PyTuple_New(0);
  if (args == NULL)
    return NULL;
  PyObject *result = Env_begin(self, args, NULL);
  Py_DECREF(args);
  return result;
}

static PyObject *Env_write(EnvObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_env(self))
    return NULL;
  MDBX_txn *txn = NULL;
  int rc;
  self->active_operations++;
  Py_BEGIN_ALLOW_THREADS
  rc = mdbx_txn_begin(self->env, NULL, MDBX_TXN_READWRITE, &txn);
  Py_END_ALLOW_THREADS
  end_env_operation(self);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_txn_begin");
  PyObject *result = new_transaction(self, NULL, txn, 0);
  if (result == NULL)
    (void)mdbx_txn_abort(txn);
  return result;
}

static int defer_write_txn_to_owner(TxnObject *self) {
  OrphanTxn *orphan = PyMem_Malloc(sizeof(*orphan));
  if (orphan == NULL) {
    /* Never attempt the upstream-forbidden cross-thread writer unlock.  In
       the extreme OOM path retain the whole wrapper as a safe leak. */
    Py_SET_REFCNT((PyObject *)self, 1);
    PyErr_NoMemory();
    PyErr_WriteUnraisable((PyObject *)self);
    return 0;
  }
  (void)mdbx_txn_break(self->txn);
  orphan->txn = self->txn;
  orphan->owner = self->owner;
  orphan->parent = self->parent;
  orphan->next = self->env->orphaned_txns;
  self->env->orphaned_txns = orphan;
  self->parent = NULL;
  self->txn = NULL;
  resolve_db_state_changes(self, 0);
  /* The active transaction count and a parent child-count, if any, remain in
     force until the owner thread actually aborts the native transaction. */
  Py_INCREF(self->env);
  if (PyErr_WarnEx(PyExc_ResourceWarning,
                   "an unfinished write transaction was finalized on a non-owner thread; "
                   "native cleanup is deferred until the owner thread next uses Environment.reap_orphaned_transactions()",
                   1) < 0)
    PyErr_WriteUnraisable((PyObject *)self);
  return 1;
}

static void Txn_dealloc(TxnObject *self) {
  if (self->txn != NULL) {
    if (self->env != NULL && self->env->pid == current_pid() && !self->readonly && self->owner != current_thread()) {
      if (!defer_write_txn_to_owner(self))
        return;
    } else {
      int rc = MDBX_SUCCESS;
      if (self->env != NULL && self->env->pid == current_pid())
        rc = mdbx_txn_abort(self->txn);
      /* Python and OS thread identifiers can eventually be reused.  Let the
         engine's authoritative ownership check override our fast pre-check;
         never discard a still-locked native writer after THREAD_MISMATCH. */
      if (!self->readonly && rc == MDBX_THREAD_MISMATCH) {
        if (!defer_write_txn_to_owner(self))
          return;
      } else {
        self->txn = NULL;
        if (self->env != NULL)
          resolve_db_state_changes(self, 0);
        txn_finish_bookkeeping(self);
      }
    }
  }
  Py_XDECREF(self->parent);
  Py_XDECREF(self->env);
  Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *Txn_commit(TxnObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_txn(self, 0))
    return NULL;
  if (self->active_children) {
    PyErr_SetString(BadTxnError, "cannot commit while a nested transaction is active");
    return NULL;
  }
  MDBX_txn *txn = self->txn;
  int rc;
  if (txn_chain_has_open_cursors(self)) {
    /* Commit also updates every cursor associated with this transaction and
       its nested parent chain, so retain the GIL while such cursors exist. */
    rc = mdbx_txn_commit(txn);
  } else {
    Py_BEGIN_ALLOW_THREADS
    rc = mdbx_txn_commit(txn);
    Py_END_ALLOW_THREADS
  }
  if (rc != MDBX_THREAD_MISMATCH) {
    self->txn = NULL;
    resolve_db_state_changes(self, rc == MDBX_SUCCESS);
    txn_finish_bookkeeping(self);
  }
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_txn_commit");
  Py_RETURN_NONE;
}

static PyObject *latency_to_dict(const MDBX_commit_latency *latency) {
  PyObject *dict = PyDict_New();
  if (dict == NULL)
    return NULL;
  if (dict_set_u64(dict, "preparation", latency->preparation) < 0 ||
      dict_set_u64(dict, "gc_wallclock", latency->gc_wallclock) < 0 ||
      dict_set_u64(dict, "audit", latency->audit) < 0 || dict_set_u64(dict, "write", latency->write) < 0 ||
      dict_set_u64(dict, "sync", latency->sync) < 0 || dict_set_u64(dict, "ending", latency->ending) < 0 ||
      dict_set_u64(dict, "whole", latency->whole) < 0 || dict_set_u64(dict, "gc_cputime", latency->gc_cputime) < 0) {
    Py_DECREF(dict);
    return NULL;
  }
  return dict;
}

static PyObject *Txn_commit_ex(TxnObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_txn(self, 0))
    return NULL;
  MDBX_commit_latency latency;
  memset(&latency, 0, sizeof(latency));
  MDBX_txn *txn = self->txn;
  int rc;
  if (txn_chain_has_open_cursors(self)) {
    rc = mdbx_txn_commit_ex(txn, &latency);
  } else {
    Py_BEGIN_ALLOW_THREADS
    rc = mdbx_txn_commit_ex(txn, &latency);
    Py_END_ALLOW_THREADS
  }
  if (rc != MDBX_THREAD_MISMATCH) {
    self->txn = NULL;
    resolve_db_state_changes(self, rc == MDBX_SUCCESS);
    txn_finish_bookkeeping(self);
  }
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_txn_commit_ex");
  return latency_to_dict(&latency);
}

static PyObject *Txn_abort(TxnObject *self, PyObject *Py_UNUSED(ignored)) {
  if (self->finished || self->txn == NULL)
    Py_RETURN_NONE;
  if (self->owner != current_thread()) {
    PyErr_SetString(ThreadError, "transaction must be aborted by its owner thread");
    return NULL;
  }
  if (!check_env(self->env))
    return NULL;
  if (self->active_children) {
    PyErr_SetString(BadTxnError, "cannot abort while a nested transaction is active");
    return NULL;
  }
  int rc = mdbx_txn_abort(self->txn);
  if (rc != MDBX_THREAD_MISMATCH) {
    self->txn = NULL;
    resolve_db_state_changes(self, 0);
    txn_finish_bookkeeping(self);
  }
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_txn_abort");
  Py_RETURN_NONE;
}

static PyObject *Txn_reset(TxnObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_txn(self, 0))
    return NULL;
  if (!self->readonly) {
    PyErr_SetString(BadTxnError, "only read transactions can be reset");
    return NULL;
  }
  int rc = mdbx_txn_reset(self->txn);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_txn_reset");
  self->reset = 1;
  Py_RETURN_NONE;
}

static PyObject *Txn_renew(TxnObject *self, PyObject *Py_UNUSED(ignored)) {
  if (self->finished || self->txn == NULL) {
    PyErr_SetString(ClosedError, "transaction is finished");
    return NULL;
  }
  if (!check_env(self->env))
    return NULL;
  if (self->owner != current_thread()) {
    PyErr_SetString(ThreadError, "transaction must be renewed by its owner thread");
    return NULL;
  }
  if (!self->readonly || !self->reset) {
    PyErr_SetString(BadTxnError, "renew() requires a reset read transaction");
    return NULL;
  }
  int rc = mdbx_txn_renew(self->txn);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_txn_renew");
  self->reset = 0;
  Py_RETURN_NONE;
}

static PyObject *Txn_break_(TxnObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_txn(self, 0))
    return NULL;
  int rc = mdbx_txn_break(self->txn);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_txn_break");
  self->broken = 1;
  Py_RETURN_NONE;
}

static PyObject *Txn_enter(TxnObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_txn(self, 0))
    return NULL;
  return Py_NewRef(self);
}

static PyObject *Txn_exit(TxnObject *self, PyObject *args) {
  PyObject *exc_type, *exc, *tb;
  if (!PyArg_ParseTuple(args, "OOO:__exit__", &exc_type, &exc, &tb))
    return NULL;
  (void)exc;
  (void)tb;
  if (self->finished || self->txn == NULL)
    Py_RETURN_FALSE;
  PyObject *result;
  if (exc_type == Py_None && !self->readonly)
    result = Txn_commit(self, NULL);
  else
    result = Txn_abort(self, NULL);
  if (result == NULL)
    return NULL;
  Py_DECREF(result);
  Py_RETURN_FALSE;
}

static PyObject *Txn_get_id(TxnObject *self, void *closure) {
  (void)closure;
  if (!check_txn(self, 0))
    return NULL;
  return PyLong_FromUnsignedLongLong(mdbx_txn_id(self->txn));
}

static PyObject *Txn_get_flags(TxnObject *self, void *closure) {
  (void)closure;
  if (self->finished || self->txn == NULL)
    return PyLong_FromLong(MDBX_TXN_FINISHED);
  if (!check_env(self->env))
    return NULL;
  if (self->owner != current_thread()) {
    PyErr_SetString(ThreadError, "transactions and cursors are bound to the thread that created them");
    return NULL;
  }
  return PyLong_FromUnsignedLong((unsigned long)mdbx_txn_flags(self->txn));
}

static PyObject *Txn_get_readonly(TxnObject *self, void *closure) {
  (void)closure;
  return PyBool_FromLong(self->readonly);
}

static PyObject *Txn_get_active(TxnObject *self, void *closure) {
  (void)closure;
  return PyBool_FromLong(!self->finished && self->txn != NULL && !self->reset && !self->broken && self->env != NULL &&
                         self->env->pid == current_pid() && !self->env->closed);
}

static PyObject *Txn_info(TxnObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"scan_readers", NULL};
  int scan = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|p:info", kwlist, &scan))
    return NULL;
  if (!check_txn(self, 0))
    return NULL;
  MDBX_txn_info info;
  int rc = mdbx_txn_info(self->txn, &info, scan != 0);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_txn_info");
  PyObject *dict = PyDict_New();
  if (dict == NULL)
    return NULL;
  if (dict_set_u64(dict, "id", info.txn_id) < 0 || dict_set_u64(dict, "reader_lag", info.txn_reader_lag) < 0 ||
      dict_set_u64(dict, "space_used", info.txn_space_used) < 0 ||
      dict_set_u64(dict, "space_limit_soft", info.txn_space_limit_soft) < 0 ||
      dict_set_u64(dict, "space_limit_hard", info.txn_space_limit_hard) < 0 ||
      dict_set_u64(dict, "space_retired", info.txn_space_retired) < 0 ||
      dict_set_u64(dict, "space_leftover", info.txn_space_leftover) < 0 ||
      dict_set_u64(dict, "space_dirty", info.txn_space_dirty) < 0 || dict_set_u64(dict, "page_gets", info.txn_pget) < 0) {
    Py_DECREF(dict);
    return NULL;
  }
  return dict;
}

static PyObject *Txn_refresh(TxnObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_txn(self, 0))
    return NULL;
  if (!self->readonly) {
    PyErr_SetString(BadTxnError, "refresh() requires a read transaction");
    return NULL;
  }
  int rc = mdbx_txn_refresh(self->txn);
  if (rc != MDBX_SUCCESS && rc != MDBX_RESULT_TRUE)
    return raise_mdbx(rc, "mdbx_txn_refresh");
  return PyBool_FromLong(rc == MDBX_RESULT_TRUE);
}

static PyObject *Txn_park(TxnObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"autounpark", NULL};
  int autounpark = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|p:park", kwlist, &autounpark))
    return NULL;
  if (!check_txn(self, 0))
    return NULL;
  if (!self->readonly) {
    PyErr_SetString(BadTxnError, "park() requires a read transaction");
    return NULL;
  }
  int rc = mdbx_txn_park(self->txn, autounpark != 0);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_txn_park");
  Py_RETURN_NONE;
}

static PyObject *Txn_unpark(TxnObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"restart_if_ousted", NULL};
  int restart = 1;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|p:unpark", kwlist, &restart))
    return NULL;
  if (self->finished || self->txn == NULL) {
    PyErr_SetString(ClosedError, "transaction is finished");
    return NULL;
  }
  if (!check_env(self->env))
    return NULL;
  if (self->owner != current_thread()) {
    PyErr_SetString(ThreadError, "transaction must be unparked by its owner thread");
    return NULL;
  }
  int rc = mdbx_txn_unpark(self->txn, restart != 0);
  if (rc != MDBX_SUCCESS && rc != MDBX_RESULT_TRUE)
    return raise_mdbx(rc, "mdbx_txn_unpark");
  return PyBool_FromLong(rc == MDBX_RESULT_TRUE);
}

static PyObject *Txn_canary(TxnObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"values", NULL};
  PyObject *values = Py_None;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O:canary", kwlist, &values))
    return NULL;
  if (!check_txn(self, values != Py_None))
    return NULL;
  MDBX_canary canary;
  int rc;
  if (values != Py_None) {
    PyObject *seq = PySequence_Fast(values, "canary values must be a three-item sequence (x, y, z)");
    if (seq == NULL)
      return NULL;
    if (PySequence_Fast_GET_SIZE(seq) != 3) {
      Py_DECREF(seq);
      PyErr_SetString(PyExc_ValueError, "canary values must contain exactly x, y, z");
      return NULL;
    }
    canary.x = PyLong_AsUnsignedLongLong(PySequence_Fast_GET_ITEM(seq, 0));
    canary.y = PyLong_AsUnsignedLongLong(PySequence_Fast_GET_ITEM(seq, 1));
    canary.z = PyLong_AsUnsignedLongLong(PySequence_Fast_GET_ITEM(seq, 2));
    canary.v = 0;
    Py_DECREF(seq);
    if (PyErr_Occurred())
      return NULL;
    rc = mdbx_canary_put(self->txn, &canary);
    if (rc != MDBX_SUCCESS)
      return raise_mdbx(rc, "mdbx_canary_put");
  }
  rc = mdbx_canary_get(self->txn, &canary);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_canary_get");
  return Py_BuildValue("(KKKK)", canary.x, canary.y, canary.z, canary.v);
}

static PyObject *Txn_gc_info(TxnObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_txn(self, 0))
    return NULL;
  MDBX_gc_info_t info;
  memset(&info, 0, sizeof(info));
  int rc = mdbx_gc_info(self->txn, &info, sizeof(info), NULL, NULL);
  if (rc != MDBX_SUCCESS && rc != MDBX_NOTFOUND)
    return raise_mdbx(rc, "mdbx_gc_info");
  PyObject *dict = PyDict_New();
  if (dict == NULL)
    return NULL;
  if (dict_set_u64(dict, "pages_total", info.pages_total) < 0 ||
      dict_set_u64(dict, "pages_backed", info.pages_backed) < 0 ||
      dict_set_u64(dict, "pages_allocated", info.pages_allocated) < 0 ||
      dict_set_u64(dict, "pages_gc", info.pages_gc) < 0 ||
      dict_set_u64(dict, "pages_reclaimable", info.gc_reclaimable.pages) < 0 ||
      dict_set_u64(dict, "max_reader_lag", info.max_reader_lag) < 0 ||
      dict_set_u64(dict, "max_retained_pages", info.max_retained_pages) < 0) {
    Py_DECREF(dict);
    return NULL;
  }
  return dict;
}

static int enumerate_table_callback(void *context, const MDBX_txn *native_txn, const MDBX_val *name,
                                    MDBX_db_flags_t flags, const MDBX_stat *stat, MDBX_dbi dbi) {
  (void)native_txn;
  PyObject *list = (PyObject *)context;
  PyObject *dict = stat_to_dict(stat);
  PyObject *py_name = val_to_bytes(name);
  PyObject *py_flags = PyLong_FromUnsignedLong((unsigned long)flags);
  PyObject *py_dbi = PyLong_FromUnsignedLong((unsigned long)dbi);
  if (dict == NULL || py_name == NULL || py_flags == NULL || py_dbi == NULL ||
      PyDict_SetItemString(dict, "name", py_name) < 0 || PyDict_SetItemString(dict, "flags", py_flags) < 0 ||
      PyDict_SetItemString(dict, "dbi", py_dbi) < 0 || PyList_Append(list, dict) < 0) {
    Py_XDECREF(dict);
    Py_XDECREF(py_name);
    Py_XDECREF(py_flags);
    Py_XDECREF(py_dbi);
    return MDBX_ENOMEM;
  }
  Py_DECREF(dict);
  Py_DECREF(py_name);
  Py_DECREF(py_flags);
  Py_DECREF(py_dbi);
  return MDBX_SUCCESS;
}

static PyObject *Txn_databases(TxnObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_txn(self, 0))
    return NULL;
  PyObject *list = PyList_New(0);
  if (list == NULL)
    return NULL;
  int rc = mdbx_enumerate_tables(self->txn, enumerate_table_callback, list);
  if (PyErr_Occurred()) {
    Py_DECREF(list);
    return NULL;
  }
  if (rc != MDBX_SUCCESS) {
    Py_DECREF(list);
    return raise_mdbx(rc, "mdbx_enumerate_tables");
  }
  return list;
}

static DbObject *optional_db(TxnObject *txn, PyObject *obj, MDBX_dbi *dbi) {
  if (obj == NULL || obj == Py_None) {
    *dbi = txn->env->main_dbi;
    return NULL;
  }
  if (!PyObject_TypeCheck(obj, &DbType)) {
    PyErr_SetString(PyExc_TypeError, "db must be a Database or None");
    return (DbObject *)-1;
  }
  DbObject *db = (DbObject *)obj;
  if (!check_db(db, txn))
    return (DbObject *)-1;
  *dbi = db->state->dbi;
  return db;
}

static PyObject *new_database(EnvObject *env, MDBX_dbi dbi, PyObject *name, TxnObject *creator) {
  DbState *existing = find_db_state(env, dbi);
  if (existing != NULL && name != NULL && name != Py_None && existing->name != NULL) {
    int same_name = PyObject_RichCompareBool(existing->name, name, Py_EQ);
    if (same_name < 0)
      return NULL;
    if (!same_name)
      return raise_mdbx(MDBX_NOTFOUND, "mdbx_dbi_open (stale cached table name)");
  }
  DbObject *self = (DbObject *)DbType.tp_alloc(&DbType, 0);
  if (self == NULL)
    return NULL;
  self->state = acquire_db_state(env, dbi, creator, name);
  if (self->state == NULL) {
    Py_DECREF(self);
    return NULL;
  }
  self->env = (EnvObject *)Py_NewRef(env);
  self->name = name == NULL ? Py_NewRef(Py_None) : Py_NewRef(name);
  self->closed = 0;
  return (PyObject *)self;
}

static PyObject *Txn_open_db(TxnObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"name", "flags", "create", "accede", NULL};
  PyObject *name = Py_None;
  PyObject *flags_obj = NULL;
  uint32_t flags = 0;
  int create = 0, accede = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OOpp:open_db", kwlist, &name, &flags_obj, &create, &accede))
    return NULL;
  if (flags_obj != NULL && !parse_uint32(flags_obj, &flags, "database flags"))
    return NULL;
  if (!check_txn(self, create))
    return NULL;
  if (create)
    flags |= MDBX_CREATE;
  if (accede)
    flags |= MDBX_DB_ACCEDE;
  MDBX_dbi dbi;
  int rc;
  PyObject *stored_name = name;
  if (name == Py_None) {
    rc = mdbx_dbi_open(self->txn, NULL, (MDBX_db_flags_t)flags, &dbi);
  } else {
    Py_buffer view;
    MDBX_val name_val;
    if (!object_to_val(name, &view, &name_val, "database name"))
      return NULL;
    rc = mdbx_dbi_open2(self->txn, &name_val, (MDBX_db_flags_t)flags, &dbi);
    if (rc == MDBX_SUCCESS)
      stored_name = PyBytes_FromStringAndSize((const char *)view.buf, view.len);
    PyBuffer_Release(&view);
    if (rc == MDBX_SUCCESS && stored_name == NULL)
      return NULL;
  }
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_dbi_open");
  unsigned actual_flags = 0, state_flags = 0;
  rc = mdbx_dbi_flags_ex(self->txn, dbi, &actual_flags, &state_flags);
  if (rc != MDBX_SUCCESS) {
    if (stored_name != name)
      Py_DECREF(stored_name);
    return raise_mdbx(rc, "mdbx_dbi_flags_ex after open");
  }
  (void)actual_flags;
  TxnObject *creator = (state_flags & MDBX_DBI_CREAT) != 0 ? self : NULL;
  if (creator != NULL)
    self->has_db_changes = 1;
  PyObject *result = new_database(self->env, dbi, stored_name, creator);
  if (stored_name != name)
    Py_DECREF(stored_name);
  return result;
}

static PyObject *Env_open_db(EnvObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"name", "flags", "create", NULL};
  PyObject *name = Py_None;
  PyObject *flags_obj = NULL;
  uint32_t flags = 0;
  int create = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OOp:open_db", kwlist, &name, &flags_obj, &create))
    return NULL;
  if (flags_obj != NULL && !parse_uint32(flags_obj, &flags, "database flags"))
    return NULL;
  PyObject *txn_obj = create ? Env_write(self, NULL) : Env_read(self, NULL);
  if (txn_obj == NULL)
    return NULL;
  TxnObject *txn = (TxnObject *)txn_obj;
  PyObject *db_args = Py_BuildValue("(OIi)", name, flags, create);
  if (db_args == NULL) {
    (void)mdbx_txn_abort(txn->txn);
    txn->txn = NULL;
    txn_finish_bookkeeping(txn);
    Py_DECREF(txn_obj);
    return NULL;
  }
  PyObject *db = Txn_open_db(txn, db_args, NULL);
  Py_DECREF(db_args);
  if (db == NULL) {
    PyObject *ignored = Txn_abort(txn, NULL);
    Py_XDECREF(ignored);
    Py_DECREF(txn_obj);
    return NULL;
  }
  PyObject *done = create ? Txn_commit(txn, NULL) : Txn_abort(txn, NULL);
  Py_DECREF(txn_obj);
  if (done == NULL) {
    Py_DECREF(db);
    return NULL;
  }
  Py_DECREF(done);
  return db;
}

static int parse_fastcall_keywords(const char *function, PyObject *const *args, Py_ssize_t nargs,
                                   PyObject *kwnames, const char *const *names, Py_ssize_t count,
                                   Py_ssize_t required, PyObject **values) {
  for (Py_ssize_t i = 0; i < count; ++i)
    values[i] = NULL;
  if (nargs > count) {
    PyErr_Format(PyExc_TypeError, "%s() takes at most %zd arguments (%zd given)", function, count,
                 nargs + (kwnames == NULL ? 0 : PyTuple_GET_SIZE(kwnames)));
    return 0;
  }
  for (Py_ssize_t i = 0; i < nargs; ++i)
    values[i] = args[i];
  const Py_ssize_t nkwargs = kwnames == NULL ? 0 : PyTuple_GET_SIZE(kwnames);
  for (Py_ssize_t i = 0; i < nkwargs; ++i) {
    PyObject *keyword = PyTuple_GET_ITEM(kwnames, i);
    Py_ssize_t slot = -1;
    if (PyUnicode_Check(keyword)) {
      for (Py_ssize_t candidate = 0; candidate < count; ++candidate) {
        int equal = PyUnicode_CompareWithASCIIString(keyword, names[candidate]);
        if (equal == 0) {
          slot = candidate;
          break;
        }
        if (equal == -1 && PyErr_Occurred())
          return 0;
      }
    }
    if (slot < 0) {
      PyErr_Format(PyExc_TypeError, "%s() got an unexpected keyword argument '%U'", function, keyword);
      return 0;
    }
    if (values[slot] != NULL) {
      PyErr_Format(PyExc_TypeError, "%s() got multiple values for argument '%s'", function, names[slot]);
      return 0;
    }
    values[slot] = args[nargs + i];
  }
  for (Py_ssize_t i = 0; i < required; ++i) {
    if (values[i] == NULL) {
      PyErr_Format(PyExc_TypeError, "%s() missing required argument '%s'", function, names[i]);
      return 0;
    }
  }
  return 1;
}

static int validate_scalar_put_flags(unsigned long flags, const char *function) {
  if (flags & MDBX_RESERVE) {
    PyErr_Format(PyExc_ValueError,
                 "MDBX_RESERVE is not accepted by %s(); mapped writable pointers are not exposed", function);
    return 0;
  }
  if (flags & MDBX_MULTIPLE) {
    /* MDBX_MULTIPLE changes the ABI of the data argument from one MDBX_val to
       an array of two.  Passing it through a scalar bytes API would let the
       engine read beyond the stack object. */
    PyErr_Format(PyExc_ValueError,
                 "MDBX_MULTIPLE is not accepted by %s(); it requires the native two-MDBX_val layout", function);
    return 0;
  }
  return 1;
}

static PyObject *Txn_get(TxnObject *self, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames) {
  PyObject *key_obj, *db_obj = Py_None, *default_obj = Py_None;
  if (kwnames == NULL) {
    if (nargs < 1 || nargs > 3) {
      PyErr_Format(PyExc_TypeError, "get() takes 1 to 3 positional arguments (%zd given)", nargs);
      return NULL;
    }
    key_obj = args[0];
    if (nargs >= 2)
      db_obj = args[1];
    if (nargs >= 3)
      default_obj = args[2];
  } else {
    static const char *const names[] = {"key", "db", "default"};
    PyObject *values[3];
    if (!parse_fastcall_keywords("get", args, nargs, kwnames, names, 3, 1, values))
      return NULL;
    key_obj = values[0];
    if (values[1] != NULL)
      db_obj = values[1];
    if (values[2] != NULL)
      default_obj = values[2];
  }
  if (!check_txn(self, 0))
    return NULL;
  MDBX_dbi dbi;
  if (optional_db(self, db_obj, &dbi) == (DbObject *)-1)
    return NULL;
  Py_buffer key_view;
  MDBX_val key, data;
  if (!object_to_val(key_obj, &key_view, &key, "key"))
    return NULL;
  int rc = mdbx_get(self->txn, dbi, &key, &data);
  PyObject *result = rc == MDBX_SUCCESS ? val_to_bytes(&data) : NULL;
  PyBuffer_Release(&key_view);
  if (rc == MDBX_NOTFOUND)
    return Py_NewRef(default_obj);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_get");
  return result;
}

static PyObject *Txn_getitem(TxnObject *self, PyObject *key_obj) {
  if (!check_txn(self, 0))
    return NULL;
  Py_buffer key_view;
  MDBX_val key, data;
  if (!object_to_val(key_obj, &key_view, &key, "key"))
    return NULL;
  int rc = mdbx_get(self->txn, self->env->main_dbi, &key, &data);
  PyObject *result = rc == MDBX_SUCCESS ? val_to_bytes(&data) : NULL;
  PyBuffer_Release(&key_view);
  if (rc == MDBX_NOTFOUND) {
    PyErr_SetObject(PyExc_KeyError, key_obj);
    return NULL;
  }
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_get");
  return result;
}

static PyObject *Txn_contains(TxnObject *self, PyObject *key_obj) {
  if (!check_txn(self, 0))
    return NULL;
  Py_buffer key_view;
  MDBX_val key, data;
  if (!object_to_val(key_obj, &key_view, &key, "key"))
    return NULL;
  int rc = mdbx_get(self->txn, self->env->main_dbi, &key, &data);
  PyBuffer_Release(&key_view);
  if (rc == MDBX_NOTFOUND)
    Py_RETURN_FALSE;
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_get");
  Py_RETURN_TRUE;
}

static PyObject *Txn_put(TxnObject *self, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames) {
  PyObject *key_obj, *value_obj, *db_obj = Py_None, *flags_obj = NULL;
  if (kwnames == NULL) {
    if (nargs < 2 || nargs > 4) {
      PyErr_Format(PyExc_TypeError, "put() takes 2 to 4 positional arguments (%zd given)", nargs);
      return NULL;
    }
    key_obj = args[0];
    value_obj = args[1];
    if (nargs >= 3)
      db_obj = args[2];
    if (nargs >= 4)
      flags_obj = args[3];
  } else {
    static const char *const names[] = {"key", "value", "db", "flags"};
    PyObject *values[4];
    if (!parse_fastcall_keywords("put", args, nargs, kwnames, names, 4, 2, values))
      return NULL;
    key_obj = values[0];
    value_obj = values[1];
    if (values[2] != NULL)
      db_obj = values[2];
    flags_obj = values[3];
  }
  if (!check_txn(self, 1))
    return NULL;
  MDBX_dbi dbi;
  if (optional_db(self, db_obj, &dbi) == (DbObject *)-1)
    return NULL;
  uint32_t flags = 0;
  if (flags_obj != NULL) {
    if (!parse_uint32(flags_obj, &flags, "put flags"))
      return NULL;
  }
  if (!validate_scalar_put_flags(flags, "put"))
    return NULL;
  Py_buffer key_view, data_view;
  MDBX_val key, data;
  if (!object_to_val(key_obj, &key_view, &key, "key"))
    return NULL;
  if (!object_to_val(value_obj, &data_view, &data, "value")) {
    PyBuffer_Release(&key_view);
    return NULL;
  }
  int rc = mdbx_put(self->txn, dbi, &key, &data, (MDBX_put_flags_t)flags);
  PyBuffer_Release(&data_view);
  PyBuffer_Release(&key_view);
  if (rc == MDBX_KEYEXIST)
    Py_RETURN_FALSE;
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_put");
  Py_RETURN_TRUE;
}

static PyObject *Txn_delete(TxnObject *self, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames) {
  PyObject *key_obj, *value_obj = Py_None, *db_obj = Py_None;
  if (kwnames == NULL) {
    if (nargs < 1 || nargs > 3) {
      PyErr_Format(PyExc_TypeError, "delete() takes 1 to 3 positional arguments (%zd given)", nargs);
      return NULL;
    }
    key_obj = args[0];
    if (nargs >= 2)
      value_obj = args[1];
    if (nargs >= 3)
      db_obj = args[2];
  } else {
    static const char *const names[] = {"key", "value", "db"};
    PyObject *values[3];
    if (!parse_fastcall_keywords("delete", args, nargs, kwnames, names, 3, 1, values))
      return NULL;
    key_obj = values[0];
    if (values[1] != NULL)
      value_obj = values[1];
    if (values[2] != NULL)
      db_obj = values[2];
  }
  if (!check_txn(self, 1))
    return NULL;
  MDBX_dbi dbi;
  if (optional_db(self, db_obj, &dbi) == (DbObject *)-1)
    return NULL;
  Py_buffer key_view, data_view;
  MDBX_val key, data;
  if (!object_to_val(key_obj, &key_view, &key, "key"))
    return NULL;
  MDBX_val *data_ptr = NULL;
  if (value_obj != Py_None) {
    if (!object_to_val(value_obj, &data_view, &data, "value")) {
      PyBuffer_Release(&key_view);
      return NULL;
    }
    data_ptr = &data;
  }
  int rc = mdbx_del(self->txn, dbi, &key, data_ptr);
  if (data_ptr)
    PyBuffer_Release(&data_view);
  PyBuffer_Release(&key_view);
  if (rc == MDBX_NOTFOUND)
    Py_RETURN_FALSE;
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_del");
  Py_RETURN_TRUE;
}

static PyObject *Txn_replace(TxnObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"key", "value", "db", "flags", NULL};
  PyObject *key_obj, *value_obj, *db_obj = Py_None, *flags_obj = NULL;
  uint32_t flags = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|OO:replace", kwlist, &key_obj, &value_obj, &db_obj, &flags_obj))
    return NULL;
  if (flags_obj != NULL && !parse_uint32(flags_obj, &flags, "replace flags"))
    return NULL;
  if (!check_txn(self, 1))
    return NULL;
  if (!validate_scalar_put_flags(flags, "replace"))
    return NULL;
  MDBX_dbi dbi;
  if (optional_db(self, db_obj, &dbi) == (DbObject *)-1)
    return NULL;
  Py_buffer key_view, value_view;
  MDBX_val key, value, old;
  if (!object_to_val(key_obj, &key_view, &key, "key"))
    return NULL;
  int get_rc = mdbx_get(self->txn, dbi, &key, &old);
  PyObject *previous = NULL;
  if (get_rc == MDBX_SUCCESS)
    previous = val_to_bytes(&old);
  else if (get_rc == MDBX_NOTFOUND)
    previous = Py_NewRef(Py_None);
  else {
    PyBuffer_Release(&key_view);
    return raise_mdbx(get_rc, "mdbx_get");
  }
  if (previous == NULL || !object_to_val(value_obj, &value_view, &value, "value")) {
    Py_XDECREF(previous);
    PyBuffer_Release(&key_view);
    return NULL;
  }
  int rc = mdbx_put(self->txn, dbi, &key, &value, (MDBX_put_flags_t)flags);
  PyBuffer_Release(&value_view);
  PyBuffer_Release(&key_view);
  if (rc != MDBX_SUCCESS) {
    Py_DECREF(previous);
    return raise_mdbx(rc, "mdbx_put");
  }
  return previous;
}

static int copy_owned_val(PyObject *obj, MDBX_val *val, char **owned, const char *label) {
  Py_buffer view;
  MDBX_val borrowed;
  *owned = NULL;
  if (!object_to_val(obj, &view, &borrowed, label))
    return 0;
  if (borrowed.iov_len != 0) {
    *owned = PyMem_Malloc(borrowed.iov_len);
    if (*owned == NULL) {
      PyBuffer_Release(&view);
      PyErr_NoMemory();
      return 0;
    }
    memcpy(*owned, borrowed.iov_base, borrowed.iov_len);
  }
  val->iov_base = *owned;
  val->iov_len = borrowed.iov_len;
  PyBuffer_Release(&view);
  return 1;
}

static void free_owned_vals(char **owned, Py_ssize_t count) {
  if (owned != NULL) {
    for (Py_ssize_t i = 0; i < count; ++i)
      PyMem_Free(owned[i]);
  }
  PyMem_Free(owned);
}

static PyObject *Txn_get_many(TxnObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"keys", "db", "default", "detached", NULL};
  PyObject *keys_obj, *db_obj = Py_None, *default_obj = Py_None;
  int detached = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OOp:get_many", kwlist, &keys_obj, &db_obj, &default_obj,
                                   &detached))
    return NULL;
  if (!check_txn(self, 0))
    return NULL;
  MDBX_dbi dbi;
  if (optional_db(self, db_obj, &dbi) == (DbObject *)-1)
    return NULL;
  if (detached && txn_chain_has_open_cursors(self)) {
    PyErr_SetString(BusyError,
                    "detached get_many() requires all cursors in the transaction chain to be closed");
    return NULL;
  }
  PyObject *seq = PySequence_Fast(keys_obj, "keys must be iterable");
  if (seq == NULL)
    return NULL;
  Py_ssize_t count = PySequence_Fast_GET_SIZE(seq);
  if (detached && count != 0) {
    MDBX_val *keys = PyMem_Calloc((size_t)count, sizeof(*keys));
    MDBX_val *values = PyMem_Calloc((size_t)count, sizeof(*values));
    char **owned = PyMem_Calloc((size_t)count, sizeof(*owned));
    char **owned_results = PyMem_Calloc((size_t)count, sizeof(*owned_results));
    int *codes = PyMem_Calloc((size_t)count, sizeof(*codes));
    if (keys == NULL || values == NULL || owned == NULL || owned_results == NULL || codes == NULL) {
      PyMem_Free(keys);
      PyMem_Free(values);
      free_owned_vals(owned, count);
      PyMem_Free(owned_results);
      PyMem_Free(codes);
      Py_DECREF(seq);
      return PyErr_NoMemory();
    }
    for (Py_ssize_t i = 0; i < count; ++i) {
      if (!copy_owned_val(PySequence_Fast_GET_ITEM(seq, i), &keys[i], &owned[i], "key")) {
        PyMem_Free(keys);
        PyMem_Free(values);
        free_owned_vals(owned, count);
        PyMem_Free(owned_results);
        PyMem_Free(codes);
        Py_DECREF(seq);
        return NULL;
      }
    }
    int copy_error = 0;
    Py_BEGIN_ALLOW_THREADS
    for (Py_ssize_t i = 0; i < count; ++i) {
      codes[i] = mdbx_get(self->txn, dbi, &keys[i], &values[i]);
      if (codes[i] == MDBX_SUCCESS && values[i].iov_len != 0) {
        if (values[i].iov_len > (size_t)PY_SSIZE_T_MAX) {
          copy_error = 2;
          break;
        }
        owned_results[i] = PyMem_RawMalloc(values[i].iov_len);
        if (owned_results[i] == NULL) {
          copy_error = 1;
          break;
        }
        memcpy(owned_results[i], values[i].iov_base, values[i].iov_len);
        values[i].iov_base = owned_results[i];
      }
    }
    Py_END_ALLOW_THREADS
    PyMem_Free(keys);
    free_owned_vals(owned, count);
    if (copy_error != 0) {
      for (Py_ssize_t i = 0; i < count; ++i)
        PyMem_RawFree(owned_results[i]);
      PyMem_Free(owned_results);
      PyMem_Free(values);
      PyMem_Free(codes);
      Py_DECREF(seq);
      if (copy_error == 1)
        return PyErr_NoMemory();
      PyErr_SetString(PyExc_OverflowError, "libmdbx value is too large for a Python bytes object");
      return NULL;
    }

    PyObject *detached_result = PyList_New(count);
    if (detached_result == NULL) {
      for (Py_ssize_t i = 0; i < count; ++i)
        PyMem_RawFree(owned_results[i]);
      PyMem_Free(owned_results);
      PyMem_Free(values);
      PyMem_Free(codes);
      Py_DECREF(seq);
      return NULL;
    }
    for (Py_ssize_t i = 0; i < count; ++i) {
      PyObject *item;
      if (codes[i] == MDBX_NOTFOUND)
        item = Py_NewRef(default_obj);
      else if (codes[i] == MDBX_SUCCESS)
        item = val_to_bytes(&values[i]);
      else {
        raise_mdbx(codes[i], "mdbx_get (detached get_many)");
        Py_DECREF(detached_result);
        for (Py_ssize_t j = 0; j < count; ++j)
          PyMem_RawFree(owned_results[j]);
        PyMem_Free(owned_results);
        PyMem_Free(values);
        PyMem_Free(codes);
        Py_DECREF(seq);
        return NULL;
      }
      if (item == NULL) {
        Py_DECREF(detached_result);
        for (Py_ssize_t j = 0; j < count; ++j)
          PyMem_RawFree(owned_results[j]);
        PyMem_Free(owned_results);
        PyMem_Free(values);
        PyMem_Free(codes);
        Py_DECREF(seq);
        return NULL;
      }
      PyList_SET_ITEM(detached_result, i, item);
    }
    for (Py_ssize_t i = 0; i < count; ++i)
      PyMem_RawFree(owned_results[i]);
    PyMem_Free(owned_results);
    PyMem_Free(values);
    PyMem_Free(codes);
    Py_DECREF(seq);
    return detached_result;
  }
  PyObject *result = PyList_New(count);
  if (result == NULL) {
    Py_DECREF(seq);
    return NULL;
  }
  for (Py_ssize_t i = 0; i < count; ++i) {
    PyObject *key_obj = PySequence_Fast_GET_ITEM(seq, i);
    Py_buffer key_view;
    MDBX_val key, data;
    if (!object_to_val(key_obj, &key_view, &key, "key"))
      goto error;
    int rc = mdbx_get(self->txn, dbi, &key, &data);
    PyBuffer_Release(&key_view);
    PyObject *item;
    if (rc == MDBX_NOTFOUND)
      item = Py_NewRef(default_obj);
    else if (rc == MDBX_SUCCESS)
      item = val_to_bytes(&data);
    else {
      raise_mdbx(rc, "mdbx_get");
      goto error;
    }
    if (item == NULL)
      goto error;
    PyList_SET_ITEM(result, i, item);
  }
  Py_DECREF(seq);
  return result;
error:
  Py_DECREF(seq);
  Py_DECREF(result);
  return NULL;
}

static PyObject *Txn_put_many(TxnObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"items", "db", "flags", "detached", NULL};
  PyObject *items_obj, *db_obj = Py_None, *flags_obj = NULL;
  uint32_t flags = 0;
  int detached = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OOp:put_many", kwlist, &items_obj, &db_obj, &flags_obj, &detached))
    return NULL;
  if (flags_obj != NULL && !parse_uint32(flags_obj, &flags, "put_many flags"))
    return NULL;
  if (!check_txn(self, 1))
    return NULL;
  if (!validate_scalar_put_flags(flags, "put_many"))
    return NULL;
  MDBX_dbi dbi;
  if (optional_db(self, db_obj, &dbi) == (DbObject *)-1)
    return NULL;
  if (detached && txn_chain_has_open_cursors(self)) {
    PyErr_SetString(BusyError,
                    "detached put_many() requires all cursors in the transaction chain to be closed");
    return NULL;
  }
  PyObject *seq = PySequence_Fast(items_obj, "items must be iterable");
  if (seq == NULL)
    return NULL;
  Py_ssize_t count = PySequence_Fast_GET_SIZE(seq);
  if (detached && count != 0) {
    MDBX_val *keys = PyMem_Calloc((size_t)count, sizeof(*keys));
    MDBX_val *values = PyMem_Calloc((size_t)count, sizeof(*values));
    char **owned_keys = PyMem_Calloc((size_t)count, sizeof(*owned_keys));
    char **owned_values = PyMem_Calloc((size_t)count, sizeof(*owned_values));
    if (keys == NULL || values == NULL || owned_keys == NULL || owned_values == NULL) {
      PyMem_Free(keys);
      PyMem_Free(values);
      free_owned_vals(owned_keys, count);
      free_owned_vals(owned_values, count);
      Py_DECREF(seq);
      return PyErr_NoMemory();
    }
    for (Py_ssize_t i = 0; i < count; ++i) {
      PyObject *pair = PySequence_Fast(PySequence_Fast_GET_ITEM(seq, i), "each item must be a key/value pair");
      if (pair == NULL) {
        PyMem_Free(keys);
        PyMem_Free(values);
        free_owned_vals(owned_keys, count);
        free_owned_vals(owned_values, count);
        Py_DECREF(seq);
        return NULL;
      }
      if (PySequence_Fast_GET_SIZE(pair) != 2) {
        Py_DECREF(pair);
        PyErr_Format(PyExc_ValueError, "item %zd is not a two-item key/value pair", i);
        PyMem_Free(keys);
        PyMem_Free(values);
        free_owned_vals(owned_keys, count);
        free_owned_vals(owned_values, count);
        Py_DECREF(seq);
        return NULL;
      }
      int copied = copy_owned_val(PySequence_Fast_GET_ITEM(pair, 0), &keys[i], &owned_keys[i], "key") &&
                   copy_owned_val(PySequence_Fast_GET_ITEM(pair, 1), &values[i], &owned_values[i], "value");
      Py_DECREF(pair);
      if (!copied) {
        PyMem_Free(keys);
        PyMem_Free(values);
        free_owned_vals(owned_keys, count);
        free_owned_vals(owned_values, count);
        Py_DECREF(seq);
        return NULL;
      }
    }
    int rc = MDBX_SUCCESS;
    Py_BEGIN_ALLOW_THREADS
    for (Py_ssize_t i = 0; i < count; ++i) {
      rc = mdbx_put(self->txn, dbi, &keys[i], &values[i], (MDBX_put_flags_t)flags);
      if (rc != MDBX_SUCCESS)
        break;
    }
    Py_END_ALLOW_THREADS
    PyMem_Free(keys);
    PyMem_Free(values);
    free_owned_vals(owned_keys, count);
    free_owned_vals(owned_values, count);
    Py_DECREF(seq);
    if (rc != MDBX_SUCCESS)
      return raise_mdbx(rc, "mdbx_put (detached put_many)");
    return PyLong_FromSsize_t(count);
  }
  for (Py_ssize_t i = 0; i < count; ++i) {
    PyObject *pair = PySequence_Fast(PySequence_Fast_GET_ITEM(seq, i), "each item must be a key/value pair");
    if (pair == NULL)
      goto error;
    if (PySequence_Fast_GET_SIZE(pair) != 2) {
      Py_DECREF(pair);
      PyErr_Format(PyExc_ValueError, "item %zd is not a two-item key/value pair", i);
      goto error;
    }
    Py_buffer key_view, data_view;
    MDBX_val key, data;
    if (!object_to_val(PySequence_Fast_GET_ITEM(pair, 0), &key_view, &key, "key")) {
      Py_DECREF(pair);
      goto error;
    }
    if (!object_to_val(PySequence_Fast_GET_ITEM(pair, 1), &data_view, &data, "value")) {
      PyBuffer_Release(&key_view);
      Py_DECREF(pair);
      goto error;
    }
    int rc = mdbx_put(self->txn, dbi, &key, &data, (MDBX_put_flags_t)flags);
    PyBuffer_Release(&data_view);
    PyBuffer_Release(&key_view);
    Py_DECREF(pair);
    if (rc != MDBX_SUCCESS) {
      raise_mdbx(rc, "mdbx_put (put_many)");
      goto error;
    }
  }
  Py_DECREF(seq);
  return PyLong_FromSsize_t(count);
error:
  Py_DECREF(seq);
  return NULL;
}

static PyObject *Txn_delete_many(TxnObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"keys", "db", "detached", NULL};
  PyObject *keys_obj, *db_obj = Py_None;
  int detached = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|Op:delete_many", kwlist, &keys_obj, &db_obj, &detached))
    return NULL;
  if (!check_txn(self, 1))
    return NULL;
  MDBX_dbi dbi;
  if (optional_db(self, db_obj, &dbi) == (DbObject *)-1)
    return NULL;
  if (detached && txn_chain_has_open_cursors(self)) {
    PyErr_SetString(BusyError,
                    "detached delete_many() requires all cursors in the transaction chain to be closed");
    return NULL;
  }
  PyObject *seq = PySequence_Fast(keys_obj, "keys must be iterable");
  if (seq == NULL)
    return NULL;
  Py_ssize_t count = PySequence_Fast_GET_SIZE(seq), removed = 0;
  if (detached && count != 0) {
    MDBX_val *keys = PyMem_Calloc((size_t)count, sizeof(*keys));
    char **owned = PyMem_Calloc((size_t)count, sizeof(*owned));
    int *codes = PyMem_Calloc((size_t)count, sizeof(*codes));
    if (keys == NULL || owned == NULL || codes == NULL) {
      PyMem_Free(keys);
      free_owned_vals(owned, count);
      PyMem_Free(codes);
      Py_DECREF(seq);
      return PyErr_NoMemory();
    }
    for (Py_ssize_t i = 0; i < count; ++i) {
      if (!copy_owned_val(PySequence_Fast_GET_ITEM(seq, i), &keys[i], &owned[i], "key")) {
        PyMem_Free(keys);
        free_owned_vals(owned, count);
        PyMem_Free(codes);
        Py_DECREF(seq);
        return NULL;
      }
    }
    Py_BEGIN_ALLOW_THREADS
    for (Py_ssize_t i = 0; i < count; ++i)
      codes[i] = mdbx_del(self->txn, dbi, &keys[i], NULL);
    Py_END_ALLOW_THREADS
    PyMem_Free(keys);
    free_owned_vals(owned, count);
    Py_DECREF(seq);
    for (Py_ssize_t i = 0; i < count; ++i) {
      if (codes[i] == MDBX_SUCCESS)
        removed++;
      else if (codes[i] != MDBX_NOTFOUND) {
        int rc = codes[i];
        PyMem_Free(codes);
        return raise_mdbx(rc, "mdbx_del (detached delete_many)");
      }
    }
    PyMem_Free(codes);
    return PyLong_FromSsize_t(removed);
  }
  for (Py_ssize_t i = 0; i < count; ++i) {
    Py_buffer key_view;
    MDBX_val key;
    if (!object_to_val(PySequence_Fast_GET_ITEM(seq, i), &key_view, &key, "key")) {
      Py_DECREF(seq);
      return NULL;
    }
    int rc = mdbx_del(self->txn, dbi, &key, NULL);
    PyBuffer_Release(&key_view);
    if (rc == MDBX_SUCCESS)
      removed++;
    else if (rc != MDBX_NOTFOUND) {
      Py_DECREF(seq);
      return raise_mdbx(rc, "mdbx_del (delete_many)");
    }
  }
  Py_DECREF(seq);
  return PyLong_FromSsize_t(removed);
}

static PyObject *Txn_stat(TxnObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"db", NULL};
  PyObject *db_obj = Py_None;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O:stat", kwlist, &db_obj))
    return NULL;
  if (!check_txn(self, 0))
    return NULL;
  MDBX_dbi dbi;
  if (optional_db(self, db_obj, &dbi) == (DbObject *)-1)
    return NULL;
  MDBX_stat stat;
  int rc = mdbx_dbi_stat(self->txn, dbi, &stat, sizeof(stat));
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_dbi_stat");
  return stat_to_dict(&stat);
}

static void Db_dealloc(DbObject *self) {
  if (self->env != NULL)
    release_db_state(self->env, &self->state);
  Py_XDECREF(self->name);
  Py_XDECREF(self->env);
  Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *Db_close(DbObject *self, PyObject *Py_UNUSED(ignored)) {
  if (self->closed)
    Py_RETURN_NONE;
  /* Repeated opens can share the same native DBI.  Closing that DBI here
     would invalidate unrelated wrappers and can race with active
     transactions.  The native handle intentionally remains environment-owned
     until drop(delete=True) or mdbx_env_close_ex(). */
  if (self->state != NULL && self->state->name != NULL)
    Py_SETREF(self->name, Py_NewRef(self->state->name));
  self->closed = 1;
  release_db_state(self->env, &self->state);
  Py_RETURN_NONE;
}

static TxnObject *Db_parse_txn(DbObject *self, PyObject *obj, int require_write) {
  if (!PyObject_TypeCheck(obj, &TxnType)) {
    PyErr_SetString(PyExc_TypeError, "txn must be a Transaction");
    return NULL;
  }
  TxnObject *txn = (TxnObject *)obj;
  if (!check_txn(txn, require_write) || !check_db(self, txn))
    return NULL;
  return txn;
}

static PyObject *Db_stat(DbObject *self, PyObject *arg) {
  TxnObject *txn = Db_parse_txn(self, arg, 0);
  if (txn == NULL)
    return NULL;
  MDBX_stat stat;
  int rc = mdbx_dbi_stat(txn->txn, self->state->dbi, &stat, sizeof(stat));
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_dbi_stat");
  return stat_to_dict(&stat);
}

static PyObject *Db_flags(DbObject *self, PyObject *arg) {
  TxnObject *txn = Db_parse_txn(self, arg, 0);
  if (txn == NULL)
    return NULL;
  unsigned flags, state;
  int rc = mdbx_dbi_flags_ex(txn->txn, self->state->dbi, &flags, &state);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_dbi_flags_ex");
  return Py_BuildValue("{sI,sI}", "flags", flags, "state", state);
}

static PyObject *Db_clear(DbObject *self, PyObject *arg) {
  TxnObject *txn = Db_parse_txn(self, arg, 1);
  if (txn == NULL)
    return NULL;
  int rc = mdbx_drop(txn->txn, self->state->dbi, false);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_drop(clear)");
  Py_RETURN_NONE;
}

static PyObject *Db_drop(DbObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"txn", "delete", NULL};
  PyObject *txn_obj;
  int delete_handle = 1;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|p:drop", kwlist, &txn_obj, &delete_handle))
    return NULL;
  TxnObject *txn = Db_parse_txn(self, txn_obj, 1);
  if (txn == NULL)
    return NULL;
  if (delete_handle) {
    Py_ssize_t transaction_chain = 0;
    for (TxnObject *scope = txn; scope != NULL; scope = scope->parent)
      transaction_chain++;
    if (self->env->active_txns > transaction_chain) {
      PyErr_SetString(BusyError,
                      "cannot delete a database while another transaction may still reference its shared DBI");
      return NULL;
    }
  }
  DbState *state = self->state;
  int rc = mdbx_drop(txn->txn, state->dbi, delete_handle != 0);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_drop");
  if (delete_handle) {
    /* libmdbx closes the DBI as part of a deleting drop.  Invalidate every
       Python alias before any can pass the stale numeric handle back to C. */
    invalidate_db_state(self->env, state);
    if (state->name != NULL)
      Py_SETREF(self->name, Py_NewRef(state->name));
    self->closed = 1;
    release_db_state(self->env, &self->state);
  }
  Py_RETURN_NONE;
}

static PyObject *Db_sequence(DbObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"txn", "increment", NULL};
  PyObject *txn_obj;
  PyObject *increment_obj = NULL;
  uint64_t increment = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|O:sequence", kwlist, &txn_obj, &increment_obj))
    return NULL;
  if (increment_obj != NULL && !parse_uint64(increment_obj, &increment, "sequence increment"))
    return NULL;
  TxnObject *txn = Db_parse_txn(self, txn_obj, increment != 0);
  if (txn == NULL)
    return NULL;
  uint64_t result;
  int rc = mdbx_dbi_sequence(txn->txn, self->state->dbi, &result, (uint64_t)increment);
  if (rc == MDBX_RESULT_TRUE) {
    PyErr_SetString(PyExc_OverflowError, "database sequence increment overflowed uint64");
    return NULL;
  }
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_dbi_sequence");
  return PyLong_FromUnsignedLongLong(result);
}

static PyObject *Db_rename(DbObject *self, PyObject *args) {
  PyObject *txn_obj, *name_obj;
  if (!PyArg_ParseTuple(args, "OO:rename", &txn_obj, &name_obj))
    return NULL;
  TxnObject *txn = Db_parse_txn(self, txn_obj, 1);
  if (txn == NULL)
    return NULL;
  if (self->state->rename_txn != NULL && self->state->rename_txn != txn) {
    PyErr_SetString(BadTxnError, "database already has a rename pending in another transaction level");
    return NULL;
  }
  Py_buffer view;
  MDBX_val name;
  if (!object_to_val(name_obj, &view, &name, "database name"))
    return NULL;
  PyObject *stored = PyBytes_FromStringAndSize((const char *)view.buf, view.len);
  if (stored == NULL) {
    PyBuffer_Release(&view);
    return NULL;
  }
  int rc = mdbx_dbi_rename2(txn->txn, self->state->dbi, &name);
  PyBuffer_Release(&view);
  if (rc != MDBX_SUCCESS) {
    Py_DECREF(stored);
    return raise_mdbx(rc, "mdbx_dbi_rename2");
  }
  if (self->state->rename_txn == NULL) {
    self->state->rename_txn = txn;
    self->state->rename_original = Py_NewRef(self->state->name);
  }
  Py_SETREF(self->state->name, Py_NewRef(stored));
  Py_SETREF(self->name, stored);
  txn->has_db_changes = 1;
  Py_RETURN_NONE;
}

static PyObject *Db_get_name(DbObject *self, void *closure) {
  (void)closure;
  return Py_NewRef(self->state != NULL && self->state->name != NULL ? self->state->name : self->name);
}

static PyObject *Db_get_closed(DbObject *self, void *closure) {
  (void)closure;
  return PyBool_FromLong(self->closed || self->state == NULL || !self->state->valid || self->env == NULL ||
                         self->env->closed || self->env->env == NULL);
}

static PyObject *Txn_cursor(TxnObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"db", NULL};
  PyObject *db_obj = Py_None;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O:cursor", kwlist, &db_obj))
    return NULL;
  if (!check_txn(self, 0))
    return NULL;
  MDBX_dbi dbi;
  DbObject *db = optional_db(self, db_obj, &dbi);
  if (db == (DbObject *)-1)
    return NULL;
  MDBX_cursor *cursor = NULL;
  int rc = mdbx_cursor_open(self->txn, dbi, &cursor);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_cursor_open");
  CursorObject *result = (CursorObject *)CursorType.tp_alloc(&CursorType, 0);
  if (result == NULL) {
    (void)mdbx_cursor_close2(cursor);
    return NULL;
  }
  result->cursor = cursor;
  result->txn = (TxnObject *)Py_NewRef(self);
  result->db = db ? (DbObject *)Py_NewRef(db) : NULL;
  result->owner = current_thread();
  result->positioned = 0;
  result->iter_started = 0;
  result->closed = 0;
  self->open_cursors++;
  return (PyObject *)result;
}

static void Cursor_dealloc(CursorObject *self) {
  const int was_open = self->cursor != NULL;
  if (was_open && self->txn != NULL && self->txn->env != NULL && self->txn->env->pid == current_pid()) {
    int rc = mdbx_cursor_close2(self->cursor);
    if (rc != MDBX_SUCCESS) {
      raise_mdbx(rc, "finalizing cursor with mdbx_cursor_close2");
      PyErr_WriteUnraisable((PyObject *)self);
    }
  }
  self->cursor = NULL;
  if (was_open && self->txn != NULL && self->txn->open_cursors > 0)
    self->txn->open_cursors--;
  Py_XDECREF(self->db);
  Py_XDECREF(self->txn);
  Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *Cursor_close(CursorObject *self, PyObject *Py_UNUSED(ignored)) {
  if (self->closed || self->cursor == NULL)
    Py_RETURN_NONE;
  if (self->txn == NULL || self->txn->env == NULL) {
    self->cursor = NULL;
    self->closed = 1;
    Py_RETURN_NONE;
  }
  if (self->txn->env->pid != current_pid()) {
    /* Never touch a native object inherited through fork(). */
    PyErr_Format(ForkError,
                 "cursor was opened in PID %ld and cannot be closed through libmdbx in forked PID %ld",
                 self->txn->env->pid, current_pid());
    return NULL;
  }
  if (!self->txn->finished && self->owner != current_thread()) {
    PyErr_SetString(ThreadError, "an active cursor must be closed by its owner thread");
    return NULL;
  }
  int rc = mdbx_cursor_close2(self->cursor);
  self->cursor = NULL;
  self->closed = 1;
  if (self->txn->open_cursors > 0)
    self->txn->open_cursors--;
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_cursor_close2");
  Py_RETURN_NONE;
}

static PyObject *cursor_move(CursorObject *self, MDBX_cursor_op op, PyObject *key_obj, PyObject *data_obj) {
  if (!check_cursor(self, 0))
    return NULL;
  Py_buffer key_view, data_view;
  int have_key = 0, have_data = 0;
  MDBX_val key, data;
  memset(&key, 0, sizeof(key));
  memset(&data, 0, sizeof(data));
  if (key_obj != NULL) {
    if (!object_to_val(key_obj, &key_view, &key, "key"))
      return NULL;
    have_key = 1;
  }
  if (data_obj != NULL) {
    if (!object_to_val(data_obj, &data_view, &data, "value")) {
      if (have_key)
        PyBuffer_Release(&key_view);
      return NULL;
    }
    have_data = 1;
  }
  int rc = mdbx_cursor_get(self->cursor, &key, &data, op);
  if (rc == MDBX_NOTFOUND) {
    if (have_data)
      PyBuffer_Release(&data_view);
    if (have_key)
      PyBuffer_Release(&key_view);
    self->positioned = 0;
    self->iter_started = 0;
    Py_RETURN_NONE;
  }
  if (rc != MDBX_SUCCESS && rc != MDBX_RESULT_TRUE) {
    if (have_data)
      PyBuffer_Release(&data_view);
    if (have_key)
      PyBuffer_Release(&key_view);
    return raise_mdbx(rc, "mdbx_cursor_get");
  }
  PyObject *py_key = val_to_bytes(&key);
  PyObject *py_data = val_to_bytes(&data);
  if (have_data)
    PyBuffer_Release(&data_view);
  if (have_key)
    PyBuffer_Release(&key_view);
  if (py_key == NULL || py_data == NULL) {
    Py_XDECREF(py_key);
    Py_XDECREF(py_data);
    return NULL;
  }
  self->positioned = 1;
  self->iter_started = 0;
  PyObject *pair = PyTuple_Pack(2, py_key, py_data);
  Py_DECREF(py_key);
  Py_DECREF(py_data);
  return pair;
}

#define CURSOR_NOARG_METHOD(name, op)                                                                                 \
  static PyObject *Cursor_##name(CursorObject *self, PyObject *Py_UNUSED(ignored)) { return cursor_move(self, op, NULL, NULL); }

CURSOR_NOARG_METHOD(first, MDBX_FIRST)
CURSOR_NOARG_METHOD(last, MDBX_LAST)
CURSOR_NOARG_METHOD(next, MDBX_NEXT)
CURSOR_NOARG_METHOD(prev, MDBX_PREV)
CURSOR_NOARG_METHOD(current, MDBX_GET_CURRENT)
CURSOR_NOARG_METHOD(first_dup, MDBX_FIRST_DUP)
CURSOR_NOARG_METHOD(last_dup, MDBX_LAST_DUP)
CURSOR_NOARG_METHOD(next_dup, MDBX_NEXT_DUP)
CURSOR_NOARG_METHOD(prev_dup, MDBX_PREV_DUP)
CURSOR_NOARG_METHOD(next_nodup, MDBX_NEXT_NODUP)
CURSOR_NOARG_METHOD(prev_nodup, MDBX_PREV_NODUP)

static PyObject *Cursor_set(CursorObject *self, PyObject *arg) { return cursor_move(self, MDBX_SET_KEY, arg, NULL); }

static PyObject *Cursor_set_range(CursorObject *self, PyObject *arg) {
  return cursor_move(self, MDBX_SET_RANGE, arg, NULL);
}

static PyObject *Cursor_get_both(CursorObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"key", "value", "range", NULL};
  PyObject *key, *value;
  int range = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|p:get_both", kwlist, &key, &value, &range))
    return NULL;
  return cursor_move(self, range ? MDBX_GET_BOTH_RANGE : MDBX_GET_BOTH, key, value);
}

static PyObject *Cursor_count(CursorObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_cursor(self, 0))
    return NULL;
  size_t count;
  int rc = mdbx_cursor_count(self->cursor, &count);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_cursor_count");
  return PyLong_FromSize_t(count);
}

static PyObject *Cursor_put(CursorObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"key", "value", "flags", NULL};
  PyObject *key_obj, *value_obj, *flags_obj = NULL;
  uint32_t flags = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|O:put", kwlist, &key_obj, &value_obj, &flags_obj))
    return NULL;
  if (flags_obj != NULL && !parse_uint32(flags_obj, &flags, "cursor put flags"))
    return NULL;
  if (!check_cursor(self, 1))
    return NULL;
  if (!validate_scalar_put_flags(flags, "cursor.put"))
    return NULL;
  Py_buffer key_view, value_view;
  MDBX_val key, value;
  if (!object_to_val(key_obj, &key_view, &key, "key"))
    return NULL;
  if (!object_to_val(value_obj, &value_view, &value, "value")) {
    PyBuffer_Release(&key_view);
    return NULL;
  }
  int rc = mdbx_cursor_put(self->cursor, &key, &value, (MDBX_put_flags_t)flags);
  PyBuffer_Release(&value_view);
  PyBuffer_Release(&key_view);
  if (rc == MDBX_KEYEXIST)
    Py_RETURN_FALSE;
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_cursor_put");
  Py_RETURN_TRUE;
}

static PyObject *Cursor_delete(CursorObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"flags", NULL};
  PyObject *flags_obj = NULL;
  uint32_t flags = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O:delete", kwlist, &flags_obj))
    return NULL;
  if (flags_obj != NULL && !parse_uint32(flags_obj, &flags, "cursor delete flags"))
    return NULL;
  if (!check_cursor(self, 1))
    return NULL;
  int rc = mdbx_cursor_del(self->cursor, (MDBX_put_flags_t)flags);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_cursor_del");
  Py_RETURN_NONE;
}

static PyObject *Cursor_renew(CursorObject *self, PyObject *arg) {
  if (self->closed || self->cursor == NULL) {
    PyErr_SetString(ClosedError, "cursor is closed");
    return NULL;
  }
  if (!PyObject_TypeCheck(arg, &TxnType)) {
    PyErr_SetString(PyExc_TypeError, "renew() requires a Transaction");
    return NULL;
  }
  TxnObject *txn = (TxnObject *)arg;
  if (!check_txn(txn, 0))
    return NULL;
  if (!txn->readonly) {
    PyErr_SetString(BadTxnError, "cursor renew requires a read transaction");
    return NULL;
  }
  if (txn->env != self->txn->env) {
    PyErr_SetString(PyExc_ValueError, "transaction belongs to a different environment");
    return NULL;
  }
  if (!self->txn->finished && self->owner != current_thread()) {
    PyErr_SetString(ThreadError, "an active cursor must be renewed by its owner thread");
    return NULL;
  }
  if (!check_db(self->db, txn))
    return NULL;
  int rc = mdbx_cursor_renew(txn->txn, self->cursor);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_cursor_renew");
  if (self->txn != txn) {
    if (self->txn->open_cursors > 0)
      self->txn->open_cursors--;
    txn->open_cursors++;
  }
  Py_SETREF(self->txn, (TxnObject *)Py_NewRef(txn));
  self->owner = current_thread();
  self->positioned = 0;
  self->iter_started = 0;
  Py_RETURN_NONE;
}

static PyObject *Cursor_enter(CursorObject *self, PyObject *Py_UNUSED(ignored)) {
  if (!check_cursor(self, 0))
    return NULL;
  return Py_NewRef(self);
}

static PyObject *Cursor_exit(CursorObject *self, PyObject *args) {
  (void)args;
  return Cursor_close(self, NULL);
}

static PyObject *Cursor_iter(PyObject *self) { return Py_NewRef(self); }

static PyObject *Cursor_iternext(PyObject *obj) {
  CursorObject *self = (CursorObject *)obj;
  if (self->iter_started == 2) {
    PyErr_SetNone(PyExc_StopIteration);
    return NULL;
  }
  MDBX_cursor_op op = self->iter_started ? MDBX_NEXT : (self->positioned ? MDBX_GET_CURRENT : MDBX_FIRST);
  PyObject *item = cursor_move(self, op, NULL, NULL);
  if (item == NULL)
    return NULL;
  if (item == Py_None) {
    Py_DECREF(item);
    self->iter_started = 2;
    PyErr_SetNone(PyExc_StopIteration);
    return NULL;
  }
  self->iter_started = 1;
  return item;
}

static PyObject *Cursor_items(CursorObject *self, PyObject *args, PyObject *kwargs) {
  static char *kwlist[] = {"start", "stop", "limit", "reverse", NULL};
  PyObject *start = Py_None, *stop = Py_None;
  Py_ssize_t limit = 0;
  int reverse = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OOnp:items", kwlist, &start, &stop, &limit, &reverse))
    return NULL;
  if (limit < 0) {
    PyErr_SetString(PyExc_ValueError, "limit must be non-negative");
    return NULL;
  }
  PyObject *result = PyList_New(0);
  if (result == NULL)
    return NULL;
  PyObject *pair;
  if (start == Py_None)
    pair = cursor_move(self, reverse ? MDBX_LAST : MDBX_FIRST, NULL, NULL);
  else
    pair = cursor_move(self, MDBX_SET_RANGE, start, NULL);
  if (pair == NULL) {
    Py_DECREF(result);
    return NULL;
  }
  if (reverse && start != Py_None && pair == Py_None) {
    Py_DECREF(pair);
    pair = cursor_move(self, MDBX_LAST, NULL, NULL);
    if (pair == NULL) {
      Py_DECREF(result);
      return NULL;
    }
  } else if (reverse && start != Py_None && pair != Py_None) {
    PyObject *found_key = PyTuple_GET_ITEM(pair, 0);
    Py_buffer found_view = {0}, start_view = {0};
    MDBX_val found_val, start_val;
    if (!object_to_val(found_key, &found_view, &found_val, "key") ||
        !object_to_val(start, &start_view, &start_val, "start key")) {
      if (found_view.obj)
        PyBuffer_Release(&found_view);
      Py_DECREF(pair);
      Py_DECREF(result);
      return NULL;
    }
    MDBX_dbi dbi = self->db ? self->db->state->dbi : self->txn->env->main_dbi;
    int cmp = mdbx_cmp(self->txn->txn, dbi, &found_val, &start_val);
    PyBuffer_Release(&start_view);
    PyBuffer_Release(&found_view);
    if (cmp > 0) {
      Py_DECREF(pair);
      pair = cursor_move(self, MDBX_PREV, NULL, NULL);
      if (pair == NULL) {
        Py_DECREF(result);
        return NULL;
      }
    }
  }
  Py_buffer stop_view;
  MDBX_val stop_val;
  int have_stop = 0;
  if (stop != Py_None) {
    if (!object_to_val(stop, &stop_view, &stop_val, "stop key")) {
      Py_DECREF(pair);
      Py_DECREF(result);
      return NULL;
    }
    have_stop = 1;
  }
  Py_ssize_t emitted = 0;
  while (pair != Py_None && (limit == 0 || emitted < limit)) {
    if (have_stop) {
      PyObject *key_obj = PyTuple_GET_ITEM(pair, 0);
      Py_buffer key_view;
      MDBX_val key_val;
      if (!object_to_val(key_obj, &key_view, &key_val, "key")) {
        Py_DECREF(pair);
        goto items_error;
      }
      MDBX_dbi dbi = self->db ? self->db->state->dbi : self->txn->env->main_dbi;
      int cmp = mdbx_cmp(self->txn->txn, dbi, &key_val, &stop_val);
      PyBuffer_Release(&key_view);
      if ((!reverse && cmp >= 0) || (reverse && cmp <= 0)) {
        Py_DECREF(pair);
        pair = Py_NewRef(Py_None);
        break;
      }
    }
    if (PyList_Append(result, pair) < 0) {
      Py_DECREF(pair);
      goto items_error;
    }
    Py_DECREF(pair);
    emitted++;
    pair = cursor_move(self, reverse ? MDBX_PREV : MDBX_NEXT, NULL, NULL);
    if (pair == NULL)
      goto items_error;
  }
  Py_DECREF(pair);
  if (have_stop)
    PyBuffer_Release(&stop_view);
  return result;
items_error:
  if (have_stop)
    PyBuffer_Release(&stop_view);
  Py_DECREF(result);
  return NULL;
}

static int Txn_sq_contains(PyObject *obj, PyObject *key) {
  PyObject *result = Txn_contains((TxnObject *)obj, key);
  if (result == NULL)
    return -1;
  int truth = PyObject_IsTrue(result);
  Py_DECREF(result);
  return truth;
}

static PyObject *module_check_interpreter(PyObject *module, PyObject *Py_UNUSED(ignored)) {
  (void)module;
  if (PyInterpreterState_Get() != PyInterpreterState_Main()) {
    PyErr_SetString(PyExc_ImportError,
                    "clibmdbx currently supports the main CPython interpreter only; "
                    "open a separate process instead of importing it in a subinterpreter");
    return NULL;
  }
  Py_RETURN_NONE;
}

static PyObject *module_diagnostics(PyObject *module, PyObject *Py_UNUSED(ignored)) {
  (void)module;
  PyObject *dict = PyDict_New();
  PyObject *upstream = PyDict_New();
  PyObject *build = PyDict_New();
  if (dict == NULL || upstream == NULL || build == NULL)
    goto error;
  if (dict_set_str(dict, "binding_version", CLIBMDBX_VERSION) < 0 ||
      dict_set_str(dict, "release_tag", CLIBMDBX_RELEASE_TAG) < 0 ||
      dict_set_str(dict, "release_commit", CLIBMDBX_RELEASE_COMMIT) < 0 ||
      dict_set_str(dict, "archive_sha256", CLIBMDBX_ARCHIVE_SHA256) < 0 ||
      dict_set_u64(dict, "pid", (uint64_t)current_pid()) < 0 ||
      dict_set_str(dict, "python_version", Py_GetVersion()) < 0 ||
      dict_set_str(dict, "python_compiler", Py_GetCompiler()) < 0 ||
      dict_set_u64(upstream, "major", mdbx_version.major) < 0 ||
      dict_set_u64(upstream, "minor", mdbx_version.minor) < 0 ||
      dict_set_u64(upstream, "patch", mdbx_version.patch) < 0 ||
      dict_set_u64(upstream, "tweak", mdbx_version.tweak) < 0 ||
      dict_set_str(upstream, "prerelease", mdbx_version.semver_prerelease) < 0 ||
      dict_set_str(upstream, "git_datetime", mdbx_version.git.datetime) < 0 ||
      dict_set_str(upstream, "git_tree", mdbx_version.git.tree) < 0 ||
      dict_set_str(upstream, "git_commit", mdbx_version.git.commit) < 0 ||
      dict_set_str(upstream, "git_describe", mdbx_version.git.describe) < 0 ||
      dict_set_str(upstream, "expected_amalgamation_commit", CLIBMDBX_AMALGAMATION_COMMIT) < 0 ||
      dict_set_str(build, "datetime", mdbx_build.datetime) < 0 || dict_set_str(build, "target", mdbx_build.target) < 0 ||
      dict_set_str(build, "options", mdbx_build.options) < 0 ||
      dict_set_str(build, "compiler", mdbx_build.compiler) < 0 || dict_set_str(build, "flags", mdbx_build.flags) < 0 ||
      dict_set_str(build, "metadata", mdbx_build.metadata) < 0 ||
      PyDict_SetItemString(dict, "libmdbx", upstream) < 0 || PyDict_SetItemString(dict, "build", build) < 0)
    goto error;
  Py_DECREF(upstream);
  Py_DECREF(build);
  return dict;
error:
  Py_XDECREF(dict);
  Py_XDECREF(upstream);
  Py_XDECREF(build);
  return NULL;
}

static PyObject *module_readahead_reasonable(PyObject *module, PyObject *args, PyObject *kwargs) {
  (void)module;
  static char *kwlist[] = {"volume", "redundancy", NULL};
  Py_ssize_t volume;
  Py_ssize_t redundancy = 0;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "n|n:readahead_reasonable", kwlist, &volume, &redundancy))
    return NULL;
  if (volume < 0) {
    PyErr_SetString(PyExc_ValueError, "volume must be non-negative");
    return NULL;
  }
  int rc = mdbx_is_readahead_reasonable((size_t)volume, (intptr_t)redundancy);
  if (rc != MDBX_RESULT_FALSE && rc != MDBX_RESULT_TRUE)
    return raise_mdbx(rc, "mdbx_is_readahead_reasonable");
  return PyBool_FromLong(rc == MDBX_RESULT_TRUE);
}

static PyObject *module_limits(PyObject *module, PyObject *Py_UNUSED(ignored)) {
  (void)module;
  intptr_t system_page_size = 0, system_total_pages = 0, system_available_pages = 0;
  int rc = mdbx_get_sysraminfo(&system_page_size, &system_total_pages, &system_available_pages);
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_get_sysraminfo");
  if (system_page_size <= 0 || system_total_pages <= 0 || system_available_pages < 0) {
    PyErr_SetString(PyExc_RuntimeError, "mdbx_get_sysraminfo returned invalid system RAM values");
    return NULL;
  }
  PyObject *dict = PyDict_New();
  if (dict == NULL)
    return NULL;
  if (dict_set_u64(dict, "page_size_min", (uint64_t)mdbx_limits_pgsize_min()) < 0 ||
      dict_set_u64(dict, "page_size_max", (uint64_t)mdbx_limits_pgsize_max()) < 0 ||
      dict_set_u64(dict, "db_size_min", (uint64_t)mdbx_limits_dbsize_min(-1)) < 0 ||
      dict_set_u64(dict, "db_size_max", (uint64_t)mdbx_limits_dbsize_max(-1)) < 0 ||
      dict_set_u64(dict, "txn_size_max", (uint64_t)mdbx_limits_txnsize_max(-1)) < 0 ||
      dict_set_u64(dict, "key_size_max", (uint64_t)mdbx_limits_keysize_max(-1, MDBX_DB_DEFAULTS)) < 0 ||
      dict_set_u64(dict, "value_size_max", (uint64_t)mdbx_limits_valsize_max(-1, MDBX_DB_DEFAULTS)) < 0 ||
      dict_set_u64(dict, "system_page_size", (uint64_t)system_page_size) < 0 ||
      dict_set_u64(dict, "system_ram_total_pages", (uint64_t)system_total_pages) < 0 ||
      dict_set_u64(dict, "system_ram_available_pages", (uint64_t)system_available_pages) < 0) {
    Py_DECREF(dict);
    return NULL;
  }
  return dict;
}

static int delete_native_path(PyObject *path, MDBX_env_delete_mode_t mode) {
  PyObject *fspath = PyOS_FSPath(path);
  if (fspath == NULL)
    return -1;
  if (!reject_embedded_nul(fspath)) {
    Py_DECREF(fspath);
    return -1;
  }
#ifdef _WIN32
  PyObject *unicode = fspath;
  if (PyBytes_Check(fspath)) {
    unicode = PyUnicode_DecodeFSDefaultAndSize(PyBytes_AS_STRING(fspath), PyBytes_GET_SIZE(fspath));
    Py_DECREF(fspath);
    if (unicode == NULL)
      return -1;
  }
  wchar_t *wide = PyUnicode_AsWideCharString(unicode, NULL);
  Py_DECREF(unicode);
  if (wide == NULL)
    return -1;
  int rc;
  Py_BEGIN_ALLOW_THREADS
  rc = mdbx_env_deleteW(wide, mode);
  Py_END_ALLOW_THREADS
  PyMem_Free(wide);
#else
  PyObject *bytes = fspath;
  if (PyUnicode_Check(fspath)) {
    bytes = PyUnicode_EncodeFSDefault(fspath);
    Py_DECREF(fspath);
    if (bytes == NULL)
      return -1;
  }
  int rc;
  Py_BEGIN_ALLOW_THREADS
  rc = mdbx_env_delete(PyBytes_AS_STRING(bytes), mode);
  Py_END_ALLOW_THREADS
  Py_DECREF(bytes);
#endif
  return rc;
}

static PyObject *module_delete_environment(PyObject *module, PyObject *args, PyObject *kwargs) {
  (void)module;
  static char *kwlist[] = {"path", "mode", NULL};
  PyObject *path;
  int mode = MDBX_ENV_JUST_DELETE;
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|i:delete_environment", kwlist, &path, &mode))
    return NULL;
  int rc = delete_native_path(path, (MDBX_env_delete_mode_t)mode);
  if (rc == -1 && PyErr_Occurred())
    return NULL;
  if (rc != MDBX_SUCCESS)
    return raise_mdbx(rc, "mdbx_env_delete");
  Py_RETURN_NONE;
}

static PyMethodDef Env_methods[] = {
    {"begin", (PyCFunction)Env_begin, METH_VARARGS | METH_KEYWORDS, "Begin a read or write transaction."},
    {"read", (PyCFunction)Env_read, METH_NOARGS, "Begin a read transaction."},
    {"write", (PyCFunction)Env_write, METH_NOARGS, "Begin a write transaction (single writer per environment)."},
    {"open_db", (PyCFunction)Env_open_db, METH_VARARGS | METH_KEYWORDS, "Open a persistent database handle."},
    {"close", (PyCFunction)Env_close, METH_VARARGS | METH_KEYWORDS, "Close the environment idempotently."},
    {"stat", (PyCFunction)Env_stat, METH_NOARGS, "Return default table statistics."},
    {"info", (PyCFunction)Env_info, METH_NOARGS, "Return environment information."},
    {"sync", (PyCFunction)Env_sync, METH_VARARGS | METH_KEYWORDS, "Flush pending data according to MDBX rules."},
    {"copy", (PyCFunction)Env_copy, METH_VARARGS | METH_KEYWORDS, "Create a consistent environment copy."},
    {"set_geometry", (PyCFunction)Env_set_geometry, METH_VARARGS, "Set lower/now/upper/grow/shrink/pagesize geometry."},
    {"get_flags", (PyCFunction)Env_get_flags, METH_NOARGS, "Return environment flags."},
    {"set_flags", (PyCFunction)Env_set_flags, METH_VARARGS | METH_KEYWORDS, "Enable or disable mutable flags."},
    {"get_option", (PyCFunction)Env_get_option, METH_O, "Return an MDBX environment option."},
    {"set_option", (PyCFunction)Env_set_option, METH_VARARGS, "Set an MDBX environment option."},
    {"reader_check", (PyCFunction)Env_reader_check, METH_NOARGS, "Clear stale reader slots and return their count."},
    {"reap_orphaned_transactions", (PyCFunction)Env_reap_orphaned_transactions, METH_NOARGS,
     "Abort write transactions whose last Python reference was finalized away from their owner thread."},
    {"defrag", (PyCFunction)Env_defrag, METH_VARARGS | METH_KEYWORDS,
     "Run libmdbx online defragmentation without a Python callback."},
    {"warmup", (PyCFunction)Env_warmup, METH_VARARGS | METH_KEYWORDS,
     "Ask the OS to prefetch/touch/lock mapped database pages."},
    {"__enter__", (PyCFunction)Env_enter, METH_NOARGS, NULL},
    {"__exit__", (PyCFunction)Env_exit, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL}};

static PyGetSetDef Env_getset[] = {{"closed", (getter)Env_get_closed, NULL, "Whether the environment is closed.", NULL},
                                   {"path", (getter)Env_get_path, NULL, "Original path object.", NULL},
                                   {NULL, NULL, NULL, NULL, NULL}};

static PyMethodDef Txn_methods[] = {
    {"commit", (PyCFunction)Txn_commit, METH_NOARGS, "Commit and finish the transaction."},
    {"commit_ex", (PyCFunction)Txn_commit_ex, METH_NOARGS, "Commit and return stage latency counters."},
    {"abort", (PyCFunction)Txn_abort, METH_NOARGS, "Abort idempotently."},
    {"reset", (PyCFunction)Txn_reset, METH_NOARGS, "Release a read snapshot while retaining its handle."},
    {"renew", (PyCFunction)Txn_renew, METH_NOARGS, "Renew a reset read transaction."},
    {"break_", (PyCFunction)Txn_break_, METH_NOARGS, "Mark the transaction unusable."},
    {"info", (PyCFunction)Txn_info, METH_VARARGS | METH_KEYWORDS, "Return transaction statistics."},
    {"refresh", (PyCFunction)Txn_refresh, METH_NOARGS, "Refresh a read transaction to a recent snapshot."},
    {"park", (PyCFunction)Txn_park, METH_VARARGS | METH_KEYWORDS, "Park a long-lived read transaction."},
    {"unpark", (PyCFunction)Txn_unpark, METH_VARARGS | METH_KEYWORDS, "Restore a parked read transaction."},
    {"canary", (PyCFunction)Txn_canary, METH_VARARGS | METH_KEYWORDS, "Get or set environment canary markers."},
    {"gc_info", (PyCFunction)Txn_gc_info, METH_NOARGS, "Return GC/page usage information."},
    {"databases", (PyCFunction)Txn_databases, METH_NOARGS, "Enumerate named tables with flags and statistics."},
    {"open_db", (PyCFunction)Txn_open_db, METH_VARARGS | METH_KEYWORDS, "Open or create a database handle."},
    {"cursor", (PyCFunction)Txn_cursor, METH_VARARGS | METH_KEYWORDS, "Open a cursor."},
    {"get", (PyCFunction)(void (*)(void))Txn_get, METH_FASTCALL | METH_KEYWORDS,
     "get(key, db=None, default=None) -> bytes or default"},
    {"put", (PyCFunction)(void (*)(void))Txn_put, METH_FASTCALL | METH_KEYWORDS,
     "put(key, value, db=None, flags=0) -> bool"},
    {"delete", (PyCFunction)(void (*)(void))Txn_delete, METH_FASTCALL | METH_KEYWORDS,
     "delete(key, value=None, db=None) -> bool"},
    {"replace", (PyCFunction)Txn_replace, METH_VARARGS | METH_KEYWORDS, "Replace a value and return the previous bytes."},
    {"get_many", (PyCFunction)Txn_get_many, METH_VARARGS | METH_KEYWORDS,
     "Look up many keys in one C call; detached=True releases the GIL around the native loop."},
    {"put_many", (PyCFunction)Txn_put_many, METH_VARARGS | METH_KEYWORDS,
     "Put key/value pairs in one C call; detached=True copies inputs and releases the GIL once."},
    {"delete_many", (PyCFunction)Txn_delete_many, METH_VARARGS | METH_KEYWORDS,
     "Delete many keys in one C call; detached=True copies inputs and releases the GIL once."},
    {"stat", (PyCFunction)Txn_stat, METH_VARARGS | METH_KEYWORDS, "Return table statistics in this snapshot."},
    {"__enter__", (PyCFunction)Txn_enter, METH_NOARGS, NULL},
    {"__exit__", (PyCFunction)Txn_exit, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL}};

static PyGetSetDef Txn_getset[] = {{"id", (getter)Txn_get_id, NULL, "Transaction/snapshot ID.", NULL},
                                   {"flags", (getter)Txn_get_flags, NULL, "Native transaction flags/state.", NULL},
                                   {"readonly", (getter)Txn_get_readonly, NULL, "Whether this is read-only.", NULL},
                                   {"active", (getter)Txn_get_active, NULL, "Whether operations are currently valid.", NULL},
                                   {NULL, NULL, NULL, NULL, NULL}};

static PyMappingMethods Txn_mapping = {.mp_length = NULL, .mp_subscript = (binaryfunc)Txn_getitem, .mp_ass_subscript = NULL};
static PySequenceMethods Txn_sequence = {.sq_contains = Txn_sq_contains};

static PyMethodDef Db_methods[] = {
    {"close", (PyCFunction)Db_close, METH_NOARGS,
     "Close this Python database view; the shared DBI remains environment-owned."},
    {"stat", (PyCFunction)Db_stat, METH_O, "Return statistics in a transaction."},
    {"flags", (PyCFunction)Db_flags, METH_O, "Return flags and state in a transaction."},
    {"clear", (PyCFunction)Db_clear, METH_O, "Delete all records in a write transaction."},
    {"drop", (PyCFunction)Db_drop, METH_VARARGS | METH_KEYWORDS, "Clear or delete a table in a write transaction."},
    {"sequence", (PyCFunction)Db_sequence, METH_VARARGS | METH_KEYWORDS, "Read/increment the table sequence."},
    {"rename", (PyCFunction)Db_rename, METH_VARARGS, "Rename a named table in a write transaction."},
    {NULL, NULL, 0, NULL}};

static PyGetSetDef Db_getset[] = {{"name", (getter)Db_get_name, NULL, "Database name bytes, or None.", NULL},
                                  {"closed", (getter)Db_get_closed, NULL, "Whether the handle is closed.", NULL},
                                  {NULL, NULL, NULL, NULL, NULL}};

static PyMethodDef Cursor_methods[] = {
    {"close", (PyCFunction)Cursor_close, METH_NOARGS, "Close idempotently."},
    {"first", (PyCFunction)Cursor_first, METH_NOARGS, NULL},
    {"last", (PyCFunction)Cursor_last, METH_NOARGS, NULL},
    {"next", (PyCFunction)Cursor_next, METH_NOARGS, NULL},
    {"prev", (PyCFunction)Cursor_prev, METH_NOARGS, NULL},
    {"current", (PyCFunction)Cursor_current, METH_NOARGS, NULL},
    {"first_dup", (PyCFunction)Cursor_first_dup, METH_NOARGS, NULL},
    {"last_dup", (PyCFunction)Cursor_last_dup, METH_NOARGS, NULL},
    {"next_dup", (PyCFunction)Cursor_next_dup, METH_NOARGS, NULL},
    {"prev_dup", (PyCFunction)Cursor_prev_dup, METH_NOARGS, NULL},
    {"next_nodup", (PyCFunction)Cursor_next_nodup, METH_NOARGS, NULL},
    {"prev_nodup", (PyCFunction)Cursor_prev_nodup, METH_NOARGS, NULL},
    {"set", (PyCFunction)Cursor_set, METH_O, "Position at an exact key."},
    {"set_range", (PyCFunction)Cursor_set_range, METH_O, "Position at the first key >= input."},
    {"get_both", (PyCFunction)Cursor_get_both, METH_VARARGS | METH_KEYWORDS, "Position by key/value in DUPSORT."},
    {"count", (PyCFunction)Cursor_count, METH_NOARGS, "Count duplicates at the current key."},
    {"put", (PyCFunction)Cursor_put, METH_VARARGS | METH_KEYWORDS, "Put through the cursor."},
    {"delete", (PyCFunction)Cursor_delete, METH_VARARGS | METH_KEYWORDS, "Delete at the current cursor position."},
    {"renew", (PyCFunction)Cursor_renew, METH_O, "Bind a reusable read cursor to a new transaction."},
    {"items", (PyCFunction)Cursor_items, METH_VARARGS | METH_KEYWORDS, "Collect a bounded forward/reverse range in C."},
    {"__enter__", (PyCFunction)Cursor_enter, METH_NOARGS, NULL},
    {"__exit__", (PyCFunction)Cursor_exit, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL}};

static PyMethodDef module_methods[] = {
    {"_check_interpreter", (PyCFunction)module_check_interpreter, METH_NOARGS, NULL},
    {"diagnostics", (PyCFunction)module_diagnostics, METH_NOARGS, "Return binding, ABI, provenance, and build data."},
    {"limits", (PyCFunction)module_limits, METH_NOARGS, "Return platform-specific libmdbx limits."},
    {"readahead_reasonable", (PyCFunction)module_readahead_reasonable, METH_VARARGS | METH_KEYWORDS,
     "Ask libmdbx whether readahead is reasonable."},
    {"delete_environment", (PyCFunction)module_delete_environment, METH_VARARGS | METH_KEYWORDS,
     "Delete an environment using the official libmdbx API."},
    {NULL, NULL, 0, NULL}};

static int prepare_types(void) {
  EnvType.tp_name = "clibmdbx.Environment";
  EnvType.tp_basicsize = sizeof(EnvObject);
  EnvType.tp_dealloc = (destructor)Env_dealloc;
  EnvType.tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE;
  EnvType.tp_doc = "A process-bound, thread-shareable libmdbx environment.";
  EnvType.tp_methods = Env_methods;
  EnvType.tp_getset = Env_getset;
  EnvType.tp_init = (initproc)Env_init;
  EnvType.tp_new = Env_new;

  TxnType.tp_name = "clibmdbx.Transaction";
  TxnType.tp_basicsize = sizeof(TxnObject);
  TxnType.tp_dealloc = (destructor)Txn_dealloc;
  TxnType.tp_flags = Py_TPFLAGS_DEFAULT;
  TxnType.tp_doc = "An owner-thread-bound MDBX transaction.";
  TxnType.tp_methods = Txn_methods;
  TxnType.tp_getset = Txn_getset;
  TxnType.tp_as_mapping = &Txn_mapping;
  TxnType.tp_as_sequence = &Txn_sequence;

  DbType.tp_name = "clibmdbx.Database";
  DbType.tp_basicsize = sizeof(DbObject);
  DbType.tp_dealloc = (destructor)Db_dealloc;
  DbType.tp_flags = Py_TPFLAGS_DEFAULT;
  DbType.tp_doc = "An environment-scoped MDBX DBI/table handle.";
  DbType.tp_methods = Db_methods;
  DbType.tp_getset = Db_getset;

  CursorType.tp_name = "clibmdbx.Cursor";
  CursorType.tp_basicsize = sizeof(CursorObject);
  CursorType.tp_dealloc = (destructor)Cursor_dealloc;
  CursorType.tp_flags = Py_TPFLAGS_DEFAULT;
  CursorType.tp_doc = "An owner-thread-bound MDBX cursor.";
  CursorType.tp_methods = Cursor_methods;
  CursorType.tp_iter = Cursor_iter;
  CursorType.tp_iternext = Cursor_iternext;

  if (PyType_Ready(&EnvType) < 0 || PyType_Ready(&TxnType) < 0 || PyType_Ready(&DbType) < 0 ||
      PyType_Ready(&CursorType) < 0)
    return -1;
  return 0;
}

static struct PyModuleDef module_def = {PyModuleDef_HEAD_INIT, "_core", "Hand-written CPython C-API bindings to libmdbx.",
                                        -1, module_methods, NULL, NULL, NULL, NULL};

static int add_exception(PyObject *module, const char *name, PyObject *base, PyObject **storage) {
  char qualified[128];
  if (PyOS_snprintf(qualified, sizeof(qualified), "clibmdbx.%s", name) < 0)
    return -1;
  PyObject *attributes = NULL;
  if (strcmp(name, "Error") == 0) {
    attributes = Py_BuildValue("{s:i,s:s,s:s}", "code", 0, "what", "", "reason", "");
    if (attributes == NULL)
      return -1;
  }
  PyObject *exc = PyErr_NewException(qualified, base, attributes);
  Py_XDECREF(attributes);
  if (exc == NULL)
    return -1;
  if (PyModule_AddObjectRef(module, name, exc) < 0) {
    Py_DECREF(exc);
    return -1;
  }
  *storage = exc;
  return 0;
}

#define ADD_INT(module, name)                                                                                         \
  do {                                                                                                                \
    if (PyModule_AddIntConstant(module, #name, (long)(name)) < 0)                                                     \
      goto error;                                                                                                     \
  } while (0)

PyMODINIT_FUNC PyInit__core(void) {
  /* Static types and exception objects are process-global in this hot-path
     single-phase module.  Reject subinterpreters explicitly instead of
     sharing interpreter-owned PyObjects unsafely. */
  if (PyInterpreterState_Get() != PyInterpreterState_Main()) {
    PyErr_SetString(PyExc_ImportError,
                    "clibmdbx currently supports the main CPython interpreter only; "
                    "open a separate process instead of importing it in a subinterpreter");
    return NULL;
  }
  if (MDBX_VERSION_MAJOR != 0 || MDBX_VERSION_MINOR != 14 || mdbx_version.major != 0 || mdbx_version.minor != 14 ||
      mdbx_version.patch != 3 || mdbx_version.git.commit == NULL ||
      strcmp(mdbx_version.git.commit, CLIBMDBX_AMALGAMATION_COMMIT) != 0) {
    PyErr_Format(PyExc_ImportError,
                 "clibmdbx was built for embedded libmdbx 0.14.3/%s but loaded %u.%u.%u/%s",
                 CLIBMDBX_AMALGAMATION_COMMIT, (unsigned)mdbx_version.major, (unsigned)mdbx_version.minor,
                 (unsigned)mdbx_version.patch, mdbx_version.git.commit ? mdbx_version.git.commit : "unknown");
    return NULL;
  }
  if (prepare_types() < 0)
    return NULL;
  (void)mdbx_setup_debug(MDBX_LOG_WARN, MDBX_DBG_DONTCHANGE, MDBX_LOGGER_DONTCHANGE);
  PyObject *module = PyModule_Create(&module_def);
  if (module == NULL)
    return NULL;
  if (add_exception(module, "Error", PyExc_Exception, &Error) < 0 ||
      add_exception(module, "KeyExistsError", Error, &KeyExistsError) < 0 ||
      add_exception(module, "NotFoundError", Error, &NotFoundError) < 0 ||
      add_exception(module, "PageNotFoundError", Error, &PageNotFoundError) < 0 ||
      add_exception(module, "MapFullError", Error, &MapFullError) < 0 ||
      add_exception(module, "ReadersFullError", Error, &ReadersFullError) < 0 ||
      add_exception(module, "DbsFullError", Error, &DbsFullError) < 0 ||
      add_exception(module, "TxnFullError", Error, &TxnFullError) < 0 ||
      add_exception(module, "CursorFullError", Error, &CursorFullError) < 0 ||
      add_exception(module, "PageFullError", Error, &PageFullError) < 0 ||
      add_exception(module, "UnableExtendMapError", Error, &UnableExtendMapError) < 0 ||
      add_exception(module, "BadTxnError", Error, &BadTxnError) < 0 ||
      add_exception(module, "BadDbiError", Error, &BadDbiError) < 0 ||
      add_exception(module, "BadRslotError", Error, &BadRslotError) < 0 ||
      add_exception(module, "BadValueSizeError", Error, &BadValueSizeError) < 0 ||
      add_exception(module, "BusyError", Error, &BusyError) < 0 ||
      add_exception(module, "CorruptedError", Error, &CorruptedError) < 0 ||
      add_exception(module, "PanicError", Error, &PanicError) < 0 ||
      add_exception(module, "VersionMismatchError", Error, &VersionMismatchError) < 0 ||
      add_exception(module, "InvalidError", Error, &InvalidError) < 0 ||
      add_exception(module, "IncompatibleError", Error, &IncompatibleError) < 0 ||
      add_exception(module, "ProblemError", Error, &ProblemError) < 0 ||
      add_exception(module, "MultiValueError", Error, &MultiValueError) < 0 ||
      add_exception(module, "BadSignatureError", Error, &BadSignatureError) < 0 ||
      add_exception(module, "WannaRecoveryError", Error, &WannaRecoveryError) < 0 ||
      add_exception(module, "KeyMismatchError", Error, &KeyMismatchError) < 0 ||
      add_exception(module, "TooLargeError", Error, &TooLargeError) < 0 ||
      add_exception(module, "ThreadError", Error, &ThreadError) < 0 ||
      add_exception(module, "TxnOverlappingError", Error, &TxnOverlappingError) < 0 ||
      add_exception(module, "BacklogDepletedError", Error, &BacklogDepletedError) < 0 ||
      add_exception(module, "DuplicatedLockError", Error, &DuplicatedLockError) < 0 ||
      add_exception(module, "DanglingDbiError", Error, &DanglingDbiError) < 0 ||
      add_exception(module, "OustedError", Error, &OustedError) < 0 ||
      add_exception(module, "MvccRetardedError", Error, &MvccRetardedError) < 0 ||
      add_exception(module, "LaggardReaderError", Error, &LaggardReaderError) < 0 ||
      add_exception(module, "ReadonlyError", Error, &ReadonlyError) < 0 ||
      add_exception(module, "InvalidParameterError", Error, &InvalidParameterError) < 0 ||
      add_exception(module, "LockError", Error, &LockError) < 0 ||
      add_exception(module, "MemoryError", Error, &NoMemoryError) < 0 ||
      add_exception(module, "DiskError", Error, &DiskError) < 0 ||
      add_exception(module, "ForkError", Error, &ForkError) < 0 ||
      add_exception(module, "ClosedError", Error, &ClosedError) < 0)
    goto error;

  if (PyModule_AddObjectRef(module, "Environment", (PyObject *)&EnvType) < 0 ||
      PyModule_AddObjectRef(module, "Transaction", (PyObject *)&TxnType) < 0 ||
      PyModule_AddObjectRef(module, "Database", (PyObject *)&DbType) < 0 ||
      PyModule_AddObjectRef(module, "Cursor", (PyObject *)&CursorType) < 0 ||
      PyModule_AddStringConstant(module, "__version__", CLIBMDBX_VERSION) < 0 ||
      PyModule_AddStringConstant(module, "LIBMDBX_VERSION", "0.14.3") < 0)
    goto error;

  ADD_INT(module, MDBX_ENV_DEFAULTS);
  ADD_INT(module, MDBX_VALIDATION);
  ADD_INT(module, MDBX_NOSUBDIR);
  ADD_INT(module, MDBX_RDONLY);
  ADD_INT(module, MDBX_EXCLUSIVE);
  ADD_INT(module, MDBX_ACCEDE);
  ADD_INT(module, MDBX_WRITEMAP);
  ADD_INT(module, MDBX_NOSTICKYTHREADS);
  ADD_INT(module, MDBX_NORDAHEAD);
  ADD_INT(module, MDBX_NOMEMINIT);
  ADD_INT(module, MDBX_LIFORECLAIM);
  ADD_INT(module, MDBX_NOMETASYNC);
  ADD_INT(module, MDBX_SAFE_NOSYNC);
  ADD_INT(module, MDBX_UTTERLY_NOSYNC);
  ADD_INT(module, MDBX_TXN_RDONLY);
  ADD_INT(module, MDBX_TXN_RDONLY_PREPARE);
  ADD_INT(module, MDBX_TXN_TRY);
  ADD_INT(module, MDBX_REVERSEKEY);
  ADD_INT(module, MDBX_DUPSORT);
  ADD_INT(module, MDBX_INTEGERKEY);
  ADD_INT(module, MDBX_DUPFIXED);
  ADD_INT(module, MDBX_INTEGERDUP);
  ADD_INT(module, MDBX_REVERSEDUP);
  ADD_INT(module, MDBX_CREATE);
  ADD_INT(module, MDBX_DB_ACCEDE);
  ADD_INT(module, MDBX_NOOVERWRITE);
  ADD_INT(module, MDBX_NODUPDATA);
  ADD_INT(module, MDBX_CURRENT);
  ADD_INT(module, MDBX_ALLDUPS);
  ADD_INT(module, MDBX_RESERVE);
  ADD_INT(module, MDBX_APPEND);
  ADD_INT(module, MDBX_APPENDDUP);
  ADD_INT(module, MDBX_MULTIPLE);
  ADD_INT(module, MDBX_CP_COMPACT);
  ADD_INT(module, MDBX_CP_FORCE_DYNAMIC_SIZE);
  ADD_INT(module, MDBX_CP_DONT_FLUSH);
  ADD_INT(module, MDBX_CP_THROTTLE_MVCC);
  ADD_INT(module, MDBX_CP_OVERWRITE);
  ADD_INT(module, MDBX_ENV_JUST_DELETE);
  ADD_INT(module, MDBX_ENV_ENSURE_UNUSED);
  ADD_INT(module, MDBX_ENV_WAIT_FOR_UNUSED);
  ADD_INT(module, MDBX_opt_max_db);
  ADD_INT(module, MDBX_opt_max_readers);
  ADD_INT(module, MDBX_opt_sync_bytes);
  ADD_INT(module, MDBX_opt_sync_period);
  ADD_INT(module, MDBX_opt_rp_augment_limit);
  ADD_INT(module, MDBX_opt_loose_limit);
  ADD_INT(module, MDBX_opt_dp_reserve_limit);
  ADD_INT(module, MDBX_opt_txn_dp_limit);
  ADD_INT(module, MDBX_opt_txn_dp_initial);
  ADD_INT(module, MDBX_opt_spill_max_denominator);
  ADD_INT(module, MDBX_opt_spill_min_denominator);
  ADD_INT(module, MDBX_opt_spill_parent4child_denominator);
  ADD_INT(module, MDBX_opt_merge_threshold);
  ADD_INT(module, MDBX_opt_writethrough_threshold);
  ADD_INT(module, MDBX_opt_prefault_write_enable);
  ADD_INT(module, MDBX_opt_gc_time_limit);
  ADD_INT(module, MDBX_opt_prefer_waf_insteadof_balance);
  ADD_INT(module, MDBX_opt_subpage_limit);
  ADD_INT(module, MDBX_opt_subpage_room_threshold);
  ADD_INT(module, MDBX_opt_subpage_reserve_prereq);
  ADD_INT(module, MDBX_opt_subpage_reserve_limit);
  ADD_INT(module, MDBX_opt_split_reserve);
  ADD_INT(module, MDBX_opt_presync_threshold);
  ADD_INT(module, MDBX_warmup_default);
  ADD_INT(module, MDBX_warmup_force);
  ADD_INT(module, MDBX_warmup_oomsafe);
  ADD_INT(module, MDBX_warmup_lock);
  ADD_INT(module, MDBX_warmup_touchlimit);
  ADD_INT(module, MDBX_warmup_release);
  ADD_INT(module, MDBX_defrag_noobstacles);
  ADD_INT(module, MDBX_defrag_step_size);
  ADD_INT(module, MDBX_defrag_large_chunk);
  ADD_INT(module, MDBX_defrag_discontinued);
  ADD_INT(module, MDBX_defrag_laggard_reader);
  ADD_INT(module, MDBX_defrag_enough_threshold);
  ADD_INT(module, MDBX_defrag_time_limit);
  ADD_INT(module, MDBX_defrag_aborted);
  ADD_INT(module, MDBX_defrag_error);
  return module;
error:
  Py_DECREF(module);
  return NULL;
}
