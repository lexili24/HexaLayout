from tqdm import tqdm

import numpy as np
import torch
from torch.autograd import Variable
# import cv2

import code.helper as helper
import code.data_helper as data_helper

num_images = data_helper.NUM_IMAGE_PER_SAMPLE


class RandomBatchSampler(torch.utils.data.Sampler):
    """Sample random batches with sequential data samples.

    When getting inputs of [0, 1, 2, 3, 4, 5, 6, 7, 8] with batch_size=2,
    returns [4, 5], [0, 1], [8], [2, 3], [6, 7] in a random sequence.
    """
    def __init__(self,
                 sampler,
                 batch_size,
                 drop_last=False):
        self.sampler = sampler
        self.batch_size = batch_size
        self.drop_last = drop_last

    def __iter__(self):
        all_batches = []
        batch = []
        for idx in self.sampler:
            batch.append(idx)
            if len(batch) == self.batch_size:
                all_batches.append(batch)
                batch = []
        if len(batch) > 0 and not self.drop_last:
            all_batches.append(batch)

        rand_index = torch.randperm(len(all_batches)).tolist()
        for index in rand_index:
            yield all_batches[index]

    def __len__(self):
        if self.drop_last:
            return len(self.sampler) // self.batch_size
        else:
            return (len(self.sampler) + self.batch_size - 1) // self.batch_size


def evaluation(model, data_loader, device): ## changed to add bbox matrix prediction
    """
    Evaluate the model using thread score.

    Args:
        model: A trained pytorch model.
        data_loader: The dataloader for a labeled dataset.

    Returns:
        Average threat score for the entire data set.
        Predicted classification results.
    """
    model.eval()
    model.to(device)
    ts_list = []
    predicted_maps = []
    iou_list = []
    with torch.no_grad():
        for sample, target, road_image, extra in tqdm(data_loader):
            single_cam_inputs = []
            for i in range(num_images):
                single_cam_input = torch.stack([batch[i] for batch in sample])
                single_cam_input = Variable(single_cam_input).to(device)
                single_cam_inputs.append(single_cam_input)
            bbox_matrix =  torch.tensor(bounding_box_to_matrix_image(target)).to(device)
            bev_lane_output  = model(single_cam_inputs, lane = True) 
            bev_bbox_output  = model(single_cam_inputs, lane = False)
            batch_ts, predicted_road_map = get_ts_for_batch_binary(bev_lane_output, road_image)
            _, bev_bbox_pred = torch.max(bev_bbox_output, dim = 1)
            mean_iou         = compute_bbox_matrix_iou(labels= bbox_matrix, predictions = bev_bbox_pred)
            ts_list.extend(batch_ts)
            predicted_maps.append(predicted_road_map)
            iou_list.append(mean_iou)
    return np.nanmean(ts_list), predicted_maps, iou_list


def get_ts_for_batch(model_output, road_image):
    """Get average threat score for a mini-batch.

    Args:
        model_output: A matrix as the output from the classification model with a shape of
            (batch_size, num_classes, height, width).
        road_image: A matrix as the truth for the batch with a shape of
            (batch_size, height, width).

    Returns:
        Average threat score.
    """
    _, predicted_road_map = model_output.max(1)
    predicted_road_map = predicted_road_map.type(torch.BoolTensor)
    # predicted_road_map = np.argmax(bev_output.cpu().detach().numpy(), axis=1).astype(bool)

    batch_ts = []
    for batch_index in range(len(road_image)):
        sample_ts = helper.compute_ts_road_map(predicted_road_map[batch_index].cpu(),
                                               road_image[batch_index])
        batch_ts.append(sample_ts)
    return batch_ts, predicted_road_map


def get_ts_for_batch_binary(model_output, road_image):
    """Get average threat score for a mini-batch.

    Args:
        model_output: A matrix as the output from the classification model with a shape of
            (batch_size, num_classes, height, width).
        road_image: A matrix as the truth for the batch with a shape of
            (batch_size, height, width).

    Returns:
        Average threat score.
    """
#     _, predicted_road_map = model_output.max(1)
#     predicted_road_map = predicted_road_map.type(torch.BoolTensor)
    predicted_road_map = (model_output > 0.5).view(-1, 800, 800)
    # predicted_road_map = np.argmax(bev_output.cpu().detach().numpy(), axis=1).astype(bool)

    batch_ts = []
    for batch_index in range(len(road_image)):
        sample_ts = helper.compute_ts_road_map(predicted_road_map[batch_index].cpu(),
                                               road_image[batch_index])
        batch_ts.append(sample_ts)
    return batch_ts, predicted_road_map


def combine_six_to_one(samples):
    """Combine six samples or feature maps into one.
        [sample0][sample1][sample2]
        [sample3][sample4][sample5], with the second row in vertically flipped direction.

    Can also try combining them along features.

    Args:
        samples: TODO

    Returns: TODO

    """
    return torch.rot90(
        torch.cat(
            [torch.cat(samples[:3], dim=-1),
             torch.cat([torch.flip(i, dims=(-2, -1)) for i in samples[3:]], dim=-1)
            ], dim=-2), k=3, dims=(-2, -1))


def bounding_box_to_matrix_image(one_target):
    """Turn bounding box coordinates and labels to 800x800 matrix with label on the corresponding index.
    Args:
        one_target: target[i] TODO
    Returns: TODO
    """
    bounding_box_map = np.full((800, 800), 9) # make 9 the background  


    for idx, bb in enumerate(one_target['bounding_box']):
        label = one_target['category'][idx]
        min_y, min_x = np.floor((bb * 10 + 400).numpy().min(axis=1))
        max_y, max_x = np.ceil((bb * 10 + 400).numpy().max(axis=1))
        # print(min_x, max_x, min_y, max_y)
        for i in range(int(min_x), int(max_x)):
            for j in range(int(min_y), int(max_y)):
                bounding_box_map[-i][j] = label
    return bounding_box_map

def compute_bbox_matrix_iou(labels, predictions, n_classes = 10):
    '''
    given two matrices of true labels and predictions, return the mean iou over 10 classes
    TODO: change this to avg mean threat scores (to get bbox from matrix)
    '''
    mean_iou = 0.0
    seen_classes = 0

    for c in range(n_classes):
        labels_c = (labels != c)
        pred_c = (predictions != c)

        labels_c_sum = (labels_c).sum()
        pred_c_sum = (pred_c).sum()

        if (labels_c_sum > 0) or (pred_c_sum > 0):
            seen_classes += 1

            intersect = np.logical_and(labels_c, pred_c).sum()
            union = labels_c_sum + pred_c_sum - intersect

            mean_iou += intersect / union

    mean_iou = mean_iou / seen_classes if seen_classes else 0
    return mean_iou 
    # iou_thresholds = [0.5, 0.6, 0.7, 0.8, 0.9]
    # total_threat_score = 0
    # total_weight = 0
    # for threshold in iou_thresholds:
    #     tp = (mean_iou > threshold).sum()
    #     threat_score = tp * 1.0 / (num_boxes1 + num_boxes2 - tp)
    #     total_threat_score += 1.0 / threshold * threat_score
    #     total_weight += 1.0 / threshold

    # average_threat_score = total_threat_score / total_weight
    
    # return average_threat_score




# Some functions used to project 6 images and combine into one.
# Requires cv2. Not currently used in modeling.

# def perspective_transform(image):
#     height, width, _ = image.shape
#     rect = np.array([
#         [0, height//2],
#         [width - 1, height//2],
#         [width - 1, height-1],
#         [0, height-1]], dtype = "float32")
#     dst = np.array([
#         [-180, -200],
#         [width + 180, -200],
#         [width - 130, height - 1],
#         [130, height-1]], dtype = "float32")
#     M = cv2.getPerspectiveTransform(rect, dst)
#     warped = cv2.warpPerspective(image, M, (width, height))
#     return warped
#
# def image_transform_via_cv2(torch_image, angle):
#     numpy_image = torch_image.numpy().transpose(1, 2, 0)
#     perspective = perspective_transform(numpy_image)
#     rotation = rotate_image(perspective, angle)
#     numpy_transformed = torch.from_numpy(rotation)
#     torch_transformed = torch.transpose(torch.transpose(numpy_transformed, 0, 2), 1, 2)
#     return torch_transformed
#
# def rotate_image(image, angle):
#     image_center = tuple(np.array(image.shape[1::-1]) / 2)
#     rot_mat = cv2.getRotationMatrix2D(image_center, angle, 1.0)
#     result = cv2.warpAffine(image, rot_mat, image.shape[1::-1], flags=cv2.INTER_LINEAR)
#     return result
#
# def images_transform(sample, idx):
#     angle = 60
#     rotation_angle = {0:angle, 1:0, 2:-angle, 3:-angle, 4:0, 5:angle}
#     post_rotation = []
#     for image in sample:
#         transformed = image_transform_via_cv2(image, rotation_angle[idx])
#         post_rotation.append(transformed)
#     return torch.stack(post_rotation)
