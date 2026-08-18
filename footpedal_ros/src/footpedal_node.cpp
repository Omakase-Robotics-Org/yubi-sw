#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/joy.hpp>
#include <hidapi.h>
#include <cstdio>
#include <cstring>
#include <chrono>
#include <thread>
#include <mutex>
#include <atomic>
#include <std_msgs/msg/bool.hpp>

using namespace std::chrono_literals;

class FootPedalNode : public rclcpp::Node
{
public:
    FootPedalNode() : Node("footpedal_node"), dev_(nullptr), pedal_thread_(nullptr), running_(true), device_ok_(false)
    {
        latest_joy_msg_.buttons.resize(3);
        latest_joy_msg_.buttons[0] = 0;
        latest_joy_msg_.buttons[1] = 0;
        latest_joy_msg_.buttons[2] = 0;

        foot_pedal_pub_ = this->create_publisher<sensor_msgs::msg::Joy>("footpedal_states", 10);

        // The pedal thread owns dev_ exclusively: it opens the device, re-opens
        // it after an unplug (1s retry), and drops it on I/O errors. The node
        // stays alive throughout, so a pedal plugged in later just starts working.
        pedal_thread_ = std::make_unique<std::thread>(&FootPedalNode::callback_pedal_state, this);

        timer_ = this->create_wall_timer(100ms, std::bind(&FootPedalNode::publish_footpedal_state, this));

    }

    ~FootPedalNode()
    {
        running_.store(false);
        if (pedal_thread_ && pedal_thread_->joinable()) {
            pedal_thread_->join();
        }
        if (dev_) {
            hid_close(dev_);
            dev_ = nullptr;
        }
        hid_exit();
    }

private:
    hid_device *dev_;
    rclcpp::Publisher<sensor_msgs::msg::Joy>::SharedPtr foot_pedal_pub_;
    rclcpp::TimerBase::SharedPtr timer_;
    std::unique_ptr<std::thread> pedal_thread_;
    std::atomic<bool> running_;
    std::atomic<bool> device_ok_;
    std::mutex pedal_mutex_;
    sensor_msgs::msg::Joy latest_joy_msg_;

    bool open_device()  // pedal thread only
    {
        unsigned short vid_pid_pair[2] = {0x3553, 0xb001};
        dev_ = hid_open(vid_pid_pair[0], vid_pid_pair[1], nullptr);
        if (dev_ == nullptr)
        {
            return false;
        }
        hid_set_nonblocking(dev_, 1);
        RCLCPP_INFO(this->get_logger(), "Device connected with VID:PID %04hx:%04hx", vid_pid_pair[0], vid_pid_pair[1]);
        return true;
    }

    void drop_device(const char *why)  // pedal thread only
    {
        RCLCPP_ERROR(this->get_logger(), "%s - footpedal offline, retrying every 1s", why);
        if (dev_) {
            hid_close(dev_);
            dev_ = nullptr;
        }
        device_ok_.store(false);
    }

    void callback_pedal_state()
    {
        bool warned_missing = false;
        while (rclcpp::ok() && running_.load()) {
            if (dev_ == nullptr) {
                if (!open_device()) {
                    if (!warned_missing) {
                        RCLCPP_ERROR(this->get_logger(),
                                     "Cannot find footswitch device. Check connection and permissions. Retrying every 1s.");
                        warned_missing = true;
                    }
                    std::this_thread::sleep_for(1s);
                    continue;
                }
                warned_missing = false;
                device_ok_.store(true);
            }
            unsigned char query[8] = {0x01, 0x82, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00};
            unsigned char response[8];

            int r = hid_write(dev_, query, sizeof(query));
            if (r < 0) {
                drop_device("Error writing to device");
                continue;
            }
            r = hid_read(dev_, response, sizeof(response));
            if (r == 0) {
                std::this_thread::sleep_for(5ms);
                continue;
            }
            if (r < 0) {
                drop_device("Error reading from device");
                continue;
            }
            std::lock_guard<std::mutex> lock(pedal_mutex_);
            if (response[3] == 0x04 || response[4] == 0x04 || response[5] == 0x04) {
                latest_joy_msg_.buttons[0] = 1;
            } else {
                latest_joy_msg_.buttons[0] = 0;
            }
            if (response[3] == 0x05 || response[4] == 0x05 || response[5] == 0x05) {
                latest_joy_msg_.buttons[1] = 1;
            } else {
                latest_joy_msg_.buttons[1] = 0;
            }
            if (response[3] == 0x06 || response[4] == 0x06 || response[5] == 0x06) {
                latest_joy_msg_.buttons[2] = 1;
            } else {
                latest_joy_msg_.buttons[2] = 0;
            }
            std::this_thread::sleep_for(10ms);
        }
    }

    void publish_footpedal_state()
    {
        // A silent topic IS the health signal: while the pedal is gone the
        // recording gate's footpedal timeout must turn red, so never keep
        // publishing the last state of a device that is no longer there.
        if (!device_ok_.load()) {
            return;
        }
        sensor_msgs::msg::Joy joy_msg;
        joy_msg.header.stamp = this->get_clock()->now();
        joy_msg.header.frame_id = "foot_pedal";
        joy_msg.buttons.resize(3);
        std::lock_guard<std::mutex> lock(pedal_mutex_);
        joy_msg.buttons = latest_joy_msg_.buttons;
        foot_pedal_pub_->publish(joy_msg);
    }
};

int main(int argc, char *argv[])
{
    rclcpp::init(argc, argv);
    auto node = std::make_shared<FootPedalNode>();

    try {
        rclcpp::spin(node);
    } catch (const std::exception &e) {
        RCLCPP_ERROR(node->get_logger(), "Error during execution: %s", e.what());
    }

    RCLCPP_INFO(node->get_logger(), "Shutting down footpedal node...");
    rclcpp::shutdown();
    return 0;
}
