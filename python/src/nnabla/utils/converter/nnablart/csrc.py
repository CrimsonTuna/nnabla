# Copyright (c) 2017 Sony Corporation. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os

import nnabla.utils.converter

from .csrc_templates import \
    csrc_parameters_defines, \
    csrc_parameters_implements, \
    csrc_defines, \
    csrc_implements, \
    csrc_example, \
    csrc_gnumake


class CsrcExporter:

    def __init__(self, nnp):
        print('CsrcExporter')

        executor = nnabla.utils.converter.select_executor(nnp)

        # Search network.
        network = nnabla.utils.converter.search_network(
            nnp, executor.network_name)

        if network is None:
            print('Network for executor [{}] does not found.'.format(
                executor.network_name))
            return
        print('Using network [{}].'.format(executor.network_name))

        self._network_name = executor.network_name

        parameters = {}
        for p in nnp.protobuf.parameter:
            parameters[p.variable_name] = p

        variables = {}
        for v in network.variable:
            variables[v.name] = v

        self._input_variables = []
        self._num_of_inputs = len(executor.data_variable)
        self._input_buffer_sizes = []
        for n, i in enumerate(executor.data_variable):
            self._input_variables.append(i.variable_name)
            v = variables[i.variable_name]
            self._input_buffer_sizes.append(
                nnabla.utils.converter.calc_shape_size(v.shape, network.batch_size))

        self._output_variables = []
        self._num_of_outputs = len(executor.output_variable)
        self._output_buffer_sizes = []
        for n, o in enumerate(executor.output_variable):
            self._output_variables.append(o.variable_name)
            v = variables[o.variable_name]
            self._output_buffer_sizes.append(
                nnabla.utils.converter.calc_shape_size(v.shape, network.batch_size))

        self._param_variables = []
        self._num_of_params = len(executor.parameter_variable)
        for n, p in enumerate(executor.parameter_variable):
            self._param_variables.append(p.variable_name)

        self._parameters = parameters
        self._network = network
        self._function_info = nnabla.utils.converter.get_function_info()

    def export_csrc_parameters(self, dirname, name, prefix):
        parameters_h_filename = os.path.join(
            dirname, '{}_parameters.h'.format(name))
        contents = []
        contents.append(
            'void* {}_parameters[{}];'.format(name, len(self._parameters)))
        parameters_h = csrc_parameters_defines.format(name_upper=name.upper(),
                                                      parameter_defines='\n'.join(contents))
        with open(parameters_h_filename, 'w') as f:
            f.write(parameters_h)

        parameters_c_filename = os.path.join(
            dirname, '{}_parameters.c'.format(name))

        contents = []
        for n, (param_name, param) in enumerate(self._parameters.items()):
            contents.append('')
            contents.append('// {}'.format(param_name))
            contents.append('float {}_parameter{}[] = {{'.format(name, n))
            for d in param.data:
                contents.append('    {:>20},'.format(str(d)))
            contents.append('};')

        contents.append('')
        contents.append('void* {}_parameters[] ={{'.format(name))
        for n, (param_name, param) in enumerate(self._parameters.items()):
            contents.append('    (void*){}_parameter{},'.format(name, n))
        contents.append('};')

        parameters_c = csrc_parameters_implements.format(
            name=name, parameter_implements='\n'.join(contents))

        with open(parameters_c_filename, 'w') as f:
            f.write(parameters_c)

    def export_csrc_defines(self, dirname, name, prefix):
        # Input
        input_buffer_size_defines = []
        for n, s in enumerate(self._input_buffer_sizes):
            input_buffer_size_defines.append(
                '#define {}_INPUT{}_SIZE ({})'.format(prefix.upper(), n, s))

        # Output
        output_buffer_size_defines = []
        for n, s in enumerate(self._output_buffer_sizes):
            output_buffer_size_defines.append(
                '#define {}_OUTPUT{}_SIZE ({})'.format(prefix.upper(), n, s))

        # Parameter
        param_buffer = []
        if len(self._parameters) > 0:
            param_buffer.append('/// Number of parameter buffers.')
            param_buffer.append('#define {}_NUM_OF_PARAM_BUFFERS ({})'.format(
                prefix.upper(), len(self._parameters)))
            param_buffer.append('/// Parameter buffer sizes.')
            for n, (param_name, param) in enumerate(self._parameters.items()):
                size = nnabla.utils.converter.calc_shape_size(param.shape, 1)
                param_buffer.append(
                    '#define {}_PARAM{}_SIZE ({})'.format(prefix.upper(), n, size))
            param_buffer.append('/// Pointer of allocated buffer.')
            param_buffer.append(
                'float* {}_param_buffer(void* context, int index);'.format(prefix))
            param_buffer.append('')

        # Generate source
        header = csrc_defines.format(name_upper=name.upper(),
                                     prefix=prefix,
                                     prefix_upper=prefix.upper(),
                                     num_of_input_buffers=self._num_of_inputs,
                                     input_buffer_size_defines='\n'.join(
                                         input_buffer_size_defines),
                                     num_of_output_buffers=self._num_of_outputs,
                                     output_buffer_size_defines='\n'.join(
                                         output_buffer_size_defines),
                                     num_of_param_buffers=len(
                                         self._parameters),
                                     param_buffer='\n'.join(param_buffer))

        header_filename = os.path.join(dirname, '{}_inference.h'.format(name))
        with open(header_filename, 'w') as f:
            f.write(header)

    def export_csrc_implements(self, dirname, name, prefix):

        batch_size = self._network.batch_size

        # Prepare variable buffers
        variable_buffers = []
        buffer_index = {}
        for n, v in enumerate(self._network.variable):
            buffer_index[n] = n
            size = nnabla.utils.converter.calc_shape_size(v.shape, batch_size)
            variable_buffers.append(size)

        # Internal definitions for context.
        internal_defines = []
        internal_defines.append('typedef struct {')
        internal_defines.append(
            '    float* variable_buffers[{}];'.format(len(variable_buffers)))
        internal_defines.append(
            '    rt_buffer_allocate_type_t variable_buffers_allocate_type[{}];'.format(len(variable_buffers)))
        internal_defines.append('')
        internal_defines.append('    // Variables')
        for n, v in enumerate(self._network.variable):
            vsize = nnabla.utils.converter.calc_shape_size(v.shape, batch_size)
            internal_defines.append(
                '    rt_variable_t v{}; ///< {}'.format(n, v.name))
            internal_defines.append(
                '    int v{}_shape[{}];'.format(n, len(v.shape.dim)))

        internal_defines.append('')
        internal_defines.append('    // Fnctions')
        for n, f in enumerate(self._network.function):
            internal_defines.append(
                '    rt_function_t f{}; ///< {}'.format(n, f.name))
            finfo = self._function_info[f.name]
            internal_defines.append(
                '    rt_variable_t* f{0}_input[{1}];'.format(n, len(finfo['input'])))
            internal_defines.append(
                '    rt_variable_t* f{0}_output[{1}];'.format(n, len(finfo['output'])))
            if 'argument' in finfo:
                internal_defines.append(
                    '    {}_config_t f{}_config;'.format(finfo['snakecase_name'], n))
                for arg_name, arg in finfo['argument'].items():
                    val = eval('f.{}_param.{}'.format(
                        finfo['snakecase_name'], arg_name))
                    if arg['Type'] == 'Shape':
                        internal_defines.append(
                            '    int f{}_config_shape_{}[{}];'.format(n, arg_name, len(val.dim)))
                    elif arg['Type'] == 'repeated int64':
                        internal_defines.append(
                            '    int f{}_config_shape_{}[{}];'.format(n, arg_name, len(val)))

        internal_defines.append('}} {}_local_context_t;'.format(prefix))

        # NAME_allocate_context
        initialize_context = []
        initialize_context.append('    // Variable buffer')
        initialize_context.append('    if(params) {')
        for n, size in enumerate(variable_buffers):
            vname = self._network.variable[n].name
            if vname in self._parameters:
                initialize_context.append(
                    '        c->variable_buffers_allocate_type[{}] = RT_BUFFER_ALLOCATE_TYPE_ALLOCATED;'.format(n))
                initialize_context.append(
                    '        c->variable_buffers[{}] = *params++;'.format(n))
            else:
                initialize_context.append(
                    '        c->variable_buffers_allocate_type[{}] = RT_BUFFER_ALLOCATE_TYPE_MALLOC;'.format(n))
                initialize_context.append(
                    '        c->variable_buffers[{}] = calloc(sizeof(float), {});'.format(n, size))
        initialize_context.append('    } else {')
        for n, size in enumerate(variable_buffers):
            initialize_context.append(
                '        c->variable_buffers_allocate_type[{}] = RT_BUFFER_ALLOCATE_TYPE_MALLOC;'.format(n))
            initialize_context.append(
                '        c->variable_buffers[{}] = calloc(sizeof(float), {});'.format(n, size))
        initialize_context.append('    }')

        variable_buffers = {}
        initialize_context.append('')
        initialize_context.append('    // Variables')
        for n, v in enumerate(self._network.variable):
            initialize_context.append('    // {}'.format(v.name))
            initialize_context.append(
                '    (c->v{}).type = NN_DATA_TYPE_FLOAT;'.format(n))
            initialize_context.append(
                '    (c->v{}).shape.size = {};'.format(n, len(v.shape.dim)))
            initialize_context.append(
                '    (c->v{0}).shape.data = c->v{0}_shape;'.format(n))
            initialize_context.append(
                '    (c->v{}).data = c->variable_buffers[{}];'.format(n, buffer_index[n]))
            variable_buffers[v.name] = '(c->v{}).data'.format(n)

        initialize_context.append('')
        initialize_context.append('    // Functions')
        for n, f in enumerate(self._network.function):
            finfo = self._function_info[f.name]
            initialize_context.append('    // {}'.format(f.name))
            initialize_context.append(
                '    (c->f{}).num_of_inputs = {};'.format(n, len(finfo['input'])))
            initialize_context.append(
                '    (c->f{0}).inputs = c->f{0}_input;'.format(n))
            initialize_context.append(
                '    (c->f{}).num_of_outputs = {};'.format(n, len(finfo['output'])))
            initialize_context.append(
                '    (c->f{0}).outputs = c->f{0}_output;'.format(n))
            if 'argument' in finfo:
                initialize_context.append(
                    '    (c->f{0}).config = &(c->f{0}_config);'.format(n))
                args = []
                for arg_name, arg in finfo['argument'].items():
                    val = eval('f.{}_param.{}'.format(
                        finfo['snakecase_name'], arg_name))
                    if arg['Type'] == 'Shape':
                        initialize_context.append(
                            '    rt_list_t arg_f{}_{};'.format(n, arg_name))
                        initialize_context.append(
                            '    arg_f{}_{}.size = {};'.format(n, arg_name, len(val.dim)))
                        initialize_context.append(
                            '    arg_f{0}_{1}.data = c->f{0}_config_shape_{1};'.format(n, arg_name))
                        for vn, v in enumerate(val.dim):
                            initialize_context.append(
                                '    arg_f{}_{}.data[{}] = {};'.format(n, arg_name, vn, v))
                        args.append('arg_f{}_{}'.format(n, arg_name))
                    elif arg['Type'] == 'repeated int64':
                        initialize_context.append(
                            '    rt_list_t arg_f{}_{};'.format(n, arg_name))
                        initialize_context.append(
                            '    arg_f{}_{}.size = {};'.format(n, arg_name, len(val)))
                        initialize_context.append(
                            '    arg_f{0}_{1}.data = c->f{0}_config_shape_{1};'.format(n, arg_name))
                        for vn, v in enumerate(val):
                            initialize_context.append(
                                '    arg_f{}_{}.data[{}] = {};'.format(n, arg_name, vn, v))
                        args.append('arg_f{}_{}'.format(n, arg_name))
                    elif arg['Type'] == 'bool':
                        if val:
                            val = 1
                        else:
                            val = 0
                        initialize_context.append(
                            '    (c->f{}_config).{} = {};'.format(n, arg_name, val))
                        args.append(str(val))
                    else:
                        initialize_context.append(
                            '    (c->f{}_config).{} = {};'.format(n, arg_name, val))
                        args.append(str(val))
                initialize_context.append(
                    '    init_{}_config(&(c->f{}_config), {});'.format(finfo['snakecase_name'], n, ', '.join(args)))
                initialize_context.append(
                    '    init_{}_local_context(&(c->f{}));'.format(finfo['snakecase_name'], n))

        # NAME_free_context
        free_context = []
        free_context.append('')
        for n, size in enumerate(variable_buffers):
            free_context.append(
                '    if(c->variable_buffers_allocate_type[{}] == RT_BUFFER_ALLOCATE_TYPE_MALLOC) {{'.format(n))
            free_context.append(
                '        free(c->variable_buffers[{}]);'.format(n))
            free_context.append('    }')

        # NAME_input_buffer
        input_buffer = []
        input_buffer.append('    switch(index) {')
        for n in range(self._num_of_inputs):
            input_buffer.append('        case {}: return {};'.format(
                n, variable_buffers[self._input_variables[n]]))
        input_buffer.append('    }')

        # NAME_output_buffer
        output_buffer = []
        output_buffer.append('    switch(index) {')
        for n in range(self._num_of_outputs):
            output_buffer.append('        case {}: return {};'.format(
                n, variable_buffers[self._output_variables[n]]))
        output_buffer.append('    }')

        # NAME_param_buffer
        param_buffer = []
        if len(self._parameters) > 0:
            param_buffer.append(
                'float* {}_param_buffer(void* context, int index)'.format(prefix))
            param_buffer.append('{')
            param_buffer.append('    WHOAMI(" %s\\n", __func__);')
            param_buffer.append(
                '    {0}_local_context_t* c = ({0}_local_context_t*)context;'.format(prefix))
            param_buffer.append('    switch(index) {')
            for n in range(self._num_of_params):
                param_buffer.append('        case {}: return {};'.format(
                    n, variable_buffers[self._param_variables[n]]))
            param_buffer.append('    }')
            param_buffer.append('    return 0;')
            param_buffer.append('}')

        # NAME_inference
        inference = []
        for n, f in enumerate(self._network.function):
            finfo = self._function_info[f.name]
            inference.append(
                '    exec_{}(&(c->f{}));'.format(finfo['snakecase_name'], n))

        # Generate source code
        source = csrc_implements.format(name=name,
                                        prefix=prefix,
                                        internal_defines='\n'.join(
                                            internal_defines),
                                        initialize_context='\n'.join(
                                            initialize_context),
                                        free_context='\n'.join(free_context),
                                        input_buffer='\n'.join(input_buffer),
                                        output_buffer='\n'.join(output_buffer),
                                        param_buffer='\n'.join(param_buffer),
                                        inference='\n'.join(inference))

        source_filename = os.path.join(dirname, '{}_inference.c'.format(name))
        with open(source_filename, 'w') as f:
            f.write(source)

    def export_csrc_example(self, dirname, name, prefix):
        includes = []
        includes.append('#include "{}_inference.h"'.format(name))
        if len(self._parameters) > 0:
            allocate = 'void *context = {}_allocate_context({}_parameters);'.format(
                prefix, name)
            includes.append('#include "{}_parameters.h"'.format(name))
        else:
            allocate = 'void *context = {}_allocate_context(0);'.format(prefix)

        prepare_input_file = []
        for n in range(self._num_of_inputs):
            prepare_input_file.append(
                '    FILE* input{} = fopen(argv[{}], "rb");'.format(n, n + 1))
            prepare_input_file.append('    assert(input{});'.format(n))
            prepare_input_file.append(
                '    int input_read_size{2} = fread({0}_input_buffer(context, {2}), sizeof(float), {1}_INPUT{2}_SIZE, input{2});'.format(prefix, prefix.upper(), n))
            prepare_input_file.append(
                '    assert(input_read_size{1} == {0}_INPUT{1}_SIZE);'.format(prefix.upper(), n))
            prepare_input_file.append('    fclose(input{});'.format(n))
            prepare_input_file.append('')

        prepare_output_file = []
        pos = self._num_of_inputs
        for n in range(self._num_of_outputs):
            prepare_output_file.append(
                '    char* output_filename{} = malloc(strlen(argv[{}]) + 10);'.format(n, pos + n + 1))
            prepare_output_file.append(
                '    sprintf(output_filename{0}, "%s_{0}.bin", argv[{1}]);'.format(n, pos + n + 1))
            prepare_output_file.append(
                '    FILE* output{0} = fopen(output_filename{0}, "wb");'.format(n))
            prepare_output_file.append('    assert(output{});'.format(n))
            prepare_output_file.append(
                '    int output_write_size{2} = fwrite({0}_output_buffer(context, {2}), sizeof(float), {1}_OUTPUT{2}_SIZE, output{2});'.format(prefix, prefix.upper(), n))
            prepare_output_file.append(
                '    assert(output_write_size{1} == {0}_OUTPUT{1}_SIZE);'.format(prefix.upper(), n))
            prepare_output_file.append('    fclose(output{});'.format(n))
            prepare_output_file.append(
                '    free(output_filename{});'.format(n))
            prepare_output_file.append('')

        example = csrc_example.format(name=name,
                                      prefix=prefix,
                                      prefix_upper=prefix.upper(),
                                      includes='\n'.join(includes),
                                      allocate=allocate,
                                      num_of_input_buffers=self._num_of_inputs,
                                      prepare_input_file='\n'.join(
                                          prepare_input_file),
                                      prepare_output_file='\n'.join(prepare_output_file))

        example_filename = os.path.join(dirname, '{}_example.c'.format(name))
        with open(example_filename, 'w') as f:
            f.write(example)

    def export_csrc_gnumake(self, dirname, name, prefix):
        param = ''
        if len(self._parameters) > 0:
            param = ' {}_parameters.c'.format(name)
        gnumake = csrc_gnumake.format(name=name, param=param)

        gnumake_filename = os.path.join(dirname, 'GNUmakefile'.format(name))
        with open(gnumake_filename, 'w') as f:
            f.write(gnumake)

    def export_csrc(self, dirname):
        name = self._network_name
        prefix = 'nnablart_{}'.format(name.lower())
        if len(self._parameters) > 0:
            self.export_csrc_parameters(dirname, name, prefix)
        self.export_csrc_defines(dirname, name, prefix)
        self.export_csrc_implements(dirname, name, prefix)
        self.export_csrc_example(dirname, name, prefix)
        self.export_csrc_gnumake(dirname, name, prefix)

    def export(self, *args):
        print('CsrcExporter.export')
        if len(args) == 1:
            if os.path.isdir(args[0]):
                self.export_csrc(args[0])
