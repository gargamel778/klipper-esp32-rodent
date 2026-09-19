execute_process(
    WORKING_DIRECTORY "${TESTDIR}"
    COMMAND make env_for_cmake
    COMMAND grep -A9 ^__ENV_START__
    COMMAND grep -B9 ^__ENV_END__
    COMMAND grep -v -e ^__ENV_START__ -e ^__ENV_END__
    COMMAND_ERROR_IS_FATAL ANY
    OUTPUT_VARIABLE ENV_FROM_MAKE
)
string(REPLACE "\n" ";" LST "${ENV_FROM_MAKE}")
list(LENGTH LST N)
message(STATUS "element count = ${N}")
set(i 0)
foreach(V ${LST})
    string(LENGTH "${V}" L)
    string(SUBSTRING "${V}" 0 40 HEAD)
    message(STATUS "  [${i}] len=${L} : '${HEAD}'")
    math(EXPR i "${i}+1")
endforeach()
