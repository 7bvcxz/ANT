# Copyright (c) 2017-2020 SKKU ESLAB, and contributors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import os

import tvm
from tvm import rpc
from tvm.contrib import graph_runtime as runtime

import nnstreamer_python as nns
import numpy as np

#import bbox

def shape_str_to_npshape(shape_str):
    shape_str_tokens = shape_str.split(":")
    return [int(token) for token in shape_str_tokens]

def shapes_str_to_npshapes(shapes_str):
    shapes_str_tokens = shapes_str.split(",")
    return [shape_str_to_npshape(token) for token in shapes_str_tokens]

def datatype_str_to_nptype(datatype_str):
    ret = None
    if datatype_str == "float32":
        ret = np.float32
    elif datatype_str == "int32":
        ret = np.int32
    elif datatype_str == "uint8":
        ret = np.uint8
    return ret

def datatypes_str_to_nptypes(datatypes_str):
    datatypes_str_tokens = datatypes_str.split(",")
    return [datatype_str_to_nptype(token) for token in datatypes_str_tokens]

def names_str_to_strarray(names_str):
    names_str_tokens = names_str.split(",")
    return [token for token in names_str_tokens]

def transform_image(image):
    # TODO: Hardcoded ImageNet dataset mean
    image = np.array(image) - np.array([103.939, 116.779, 123.68])
    image = image / np.array([57.375, 57.12, 58.395])
    image = image.transpose((2, 0, 1))
    image = image[np.newaxis, :]
    return image

def nms(self, detected):
    threshold_iou = 0.5
    detected = sorted(detected, key=lambda a: a['prob'])
    boxes_size = len(detected)

    _del = [False for _ in range(boxes_size)]

    for i in range(boxes_size):
        if not _del[i]:
            for j in range(i + 1, boxes_size):
                if self.iou(detected[i], detected[j]) > threshold_iou:
                    _del[j] = True

    # update result
    self.detected_objects.clear()

    for i in range(boxes_size):
        if not _del[i]:
            self.detected_objects.append(detected[i])

        if DEBUG:
            print("==============================")
            print("LABEL           : {}".format(
                   self.tflite_labels[detected[i]["class_id"]]))
            print("x               : {}".format(detected[i]["x"]))
            print("y               : {}".format(detected[i]["y"]))
            print("width           : {}".format(detected[i]["width"]))
            print("height          : {}".format(detected[i]["height"]))
            print("Confidence Score: {}".format(detected[i]["prob"]))

class CustomFilter(object):
    def __init__(self, *args):
        # Parse arguments
        print(args)
        model_path = args[0]
        input_shapes = shapes_str_to_npshapes(args[1])
        input_types = datatypes_str_to_nptypes(args[2])
        output_shapes = shapes_str_to_npshapes(args[3])
        output_types = datatypes_str_to_nptypes(args[4])
        input_names = names_str_to_strarray(args[5])
        output_names = names_str_to_strarray(args[6])
        self.input_shapes = input_shapes
        for input_type in input_types:
            if input_type is None:
                print("Invalid input_type")
                return None
        for output_type in output_types:
            if output_type is None:
                print("Invalid output_type")
                return None
        if (len(input_shapes) > 4 or len(input_types) > 4 or len(input_names) > 4
                or len(input_shapes) != len(input_types)
                or len(input_shapes) != len(input_names)):
            print("Invalid input count: (%d,%d,%d)".format(
                len(input_shapes), len(input_types), len(input_names)))
            return None
        if (len(output_shapes) > 4 or len(output_types) > 4 or len(output_names) > 4
                or len(output_shapes) != len(output_types)
                or len(output_shapes) != len(output_names)):
            print("Invalid output count: (%d,%d,%d)".format(
                len(output_shapes), len(output_types), len(output_names)))
            return None
        self.input_dims = []
        self.output_dims = []
        self.input_types = input_types
        self.output_types = output_types
        for i in range(len(input_shapes)):
            input_dim = nns.TensorShape(input_shapes[i], input_types[i])
            self.input_dims.append(input_dim)
        for i in range(len(output_shapes)):
            output_dim = nns.TensorShape(output_shapes[i], output_types[i])
            self.output_dims.append(output_dim)
        self.input_names = input_names
        self.output_names = output_names

        # Initialize TVM runtime session with given binary
        session = rpc.LocalSession()
        session.upload(os.path.join(model_path, "mod.so"))
        lib = session.load_module("mod.so")
        ctx = session.cpu() # TODO: Hardcoded CPU backend

        # Load graph and create a module
        self.graph = open(os.path.join(model_path, "mod.json")).read()
        self.module = runtime.create(self.graph, lib, ctx)

        # Load params
        self.params = bytearray(open(os.path.join(model_path, "mod.params"), "rb").read())
        self.module.load_params(self.params)
        return None

    def getInputDim(self):
        # pylint: disable=invalid-name
        return self.input_dims

    def getOutputDim(self):
        # pylint: disable=invalid-name
        return self.output_dims

    def invoke(self, input_array):
        graph = self.graph
        params = self.params
        fill_mode = "random"

        # Setting input
        inputs_dict = {}
        for i in range(len(self.input_dims)):
            input_element = input_array[i]
            input_dim = self.input_dims[i]
            input_name = self.input_names[i]

            input_tensor = np.reshape(input_element, input_dim.getDims()[::-1])[i]
            input_image = transform_image(input_tensor)
            inputs_dict[input_name] = input_image
        self.module.set_input(**inputs_dict)

        # Run inference
        self.module.run()

        # Get output tensors
        outputs = []
        #print(self.output_dims)
        for i in range(len(self.output_dims)):
            #print(f"\n\n ** {i} **\n\n")
            output_element = self.module.get_output(i)
            #print(output_element)
            nptype = self.output_types[i]
            outputs.append(output_element.asnumpy().astype(nptype))

        # Post-processing
        app_output = self.postProcessing(outputs)

        return app_output

    def postProcessing(self, outputs):
        classes = outputs[0] # (1, 100, 1) : (5,1,1,1) 
        scores = outputs[1]  # (1, 100, 1) : (5,1,1,1)
        bboxes = outputs[2]  # (1, 100, 4) : (4,5,1,1)
        #print(classes.shape)
        #print(scores.shape)
        print(bboxes.shape)


        ####
        # 2. score filtering
        sel_classes = []
        sel_scores = []
        sel_bboxes = []
        
        thresh = 0
        for i, bbox in enumerate(bboxes[0]):
            if i == 3:
                break
            if scores[0][i][0] > thresh:
                sel_classes.append(classes[0][i])
                sel_scores.append(scores[0][i])
                sel_bboxes.append(bboxes[0][i])
        # TODO: if confident boxes are too few, add default boxes
        # TODO: replace this by num_detection


        #print(np.asarray([sel_classes]).shape)
        #print(np.asarray([sel_scores]).shape)
        print(np.asarray([sel_bboxes]).shape)
        app_output = [np.asarray([sel_classes]), np.asarray([sel_scores]), np.asarray([sel_bboxes])]
        #print(app_output)
        print(f'{len(sel_classes)}, {len(sel_scores)}, {len(sel_bboxes)}')
        #app_output = outputs

        return app_output
